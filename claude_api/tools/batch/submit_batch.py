"""Tool: submit_batch — submit independent requests for async 50% cost processing."""
import json
import logging

LOG = logging.getLogger("daisy")

NAME = "submit_batch"
DESCRIPTION = (
    "Submit independent Claude API requests for async processing at 50% cost. "
    "Use when you have multiple analyses that don't depend on each other's "
    "results (per-block timing triage across 20 blocks, QoR comparison across "
    "10 runs, categorizing 100 DRC violations, etc.).\n\n"
    "WORKFLOW:\n"
    "1. Preprocess data FIRST with run_command/run_python — batch prompts "
    "have NO tool access, so all relevant data must be extracted into the "
    "prompt upfront. grep/awk/python to extract just the metrics that matter.\n"
    "2. Each request's prompt should be <2KB of preprocessed data plus the "
    "analysis question. DO NOT put raw log files into batch prompts.\n"
    "3. Provide a domain-specific `system` per request (e.g. 'You are a "
    "timing-closure analyst reviewing Innovus QoR deltas.') — the generic "
    "default is too vague for most real work.\n"
    "4. submit_batch returns a batch_id and auto-creates/updates a task.\n"
    "5. Tell the user results will be ready within 24 hours (usually <1h).\n"
    "6. Next session: check_batch, then get_batch_results."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {
            "type": "string",
            "description": "Link to existing task (optional — creates new task if omitted)",
        },
        "task_name": {
            "type": "string",
            "description": "Name for auto-created task (required if no task_id)",
        },
        "model": {
            "type": "string",
            "description": "Model for batch requests (default: claude-haiku-4-5)",
        },
        "requests": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "Human-readable label (e.g. 'block_A timing')",
                    },
                    "system": {
                        "type": "string",
                        "description": "System prompt (keep minimal — default provided)",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Pre-extracted data + analysis question",
                    },
                    "max_tokens": {
                        "type": "integer",
                        "description": "Max response tokens (default: 2048)",
                    },
                },
                "required": ["label", "prompt"],
            },
            "description": "List of batch requests. Each prompt should contain PRE-EXTRACTED data.",
            "minItems": 1,
        },
    },
    "required": ["requests"],
}

_DEFAULT_BATCH_SYSTEM = (
    "You are an EDA analysis assistant reviewing pre-extracted data from "
    "Cadence Genus/Innovus runs. Answer the question directly using ONLY "
    "the data in the prompt — do not speculate about values not shown. "
    "Be concise, data-driven, and lead with the answer. For numeric "
    "comparisons, include the exact values. For categorical findings, "
    "cite the specific line or metric that supports the finding."
)
_TOKEN_WARN_THRESHOLD = 8000  # ~32KB of text


def make_handler(batch_store=None, task_store=None, config=None, audit=None, **kwargs):
    import anthropic

    def _handler(requests, task_id=None, task_name=None, model=None):
        client = anthropic.Anthropic(api_key=config.api_key)
        model = model or "claude-haiku-4-5"

        # Resolve or create task
        if task_id:
            task_data = json.loads(task_store.get_task(task_id))
            if "error" in task_data:
                return json.dumps(task_data)
        else:
            name = task_name or "Batch: %s" % requests[0]["label"]
            result = json.loads(task_store.create_task(
                name=name, description="Auto-created for batch job",
                tags=["batch"],
            ))
            task_id = result["task_id"]

        # Build batch API requests
        manifest = []
        api_requests = []
        warnings = []
        total_est_tokens = 0

        for i, req in enumerate(requests):
            custom_id = "%s__%d" % (task_id, i)
            label = req.get("label", "request_%d" % i)
            prompt = req["prompt"]
            system = req.get("system", _DEFAULT_BATCH_SYSTEM)
            max_tokens = req.get("max_tokens", 2048)

            # Token estimation and warning
            est_tokens = len(prompt) // 4 + len(system) // 4
            total_est_tokens += est_tokens
            if est_tokens > _TOKEN_WARN_THRESHOLD:
                warnings.append(
                    "Request '%s' is ~%d tokens. Consider preprocessing "
                    "with run_command/run_python to reduce." % (label, est_tokens)
                )

            manifest.append({"custom_id": custom_id, "label": label})
            api_requests.append({
                "custom_id": custom_id,
                "params": {
                    "model": model,
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": prompt}],
                },
            })

        # Submit to Anthropic Batch API
        try:
            batch = client.messages.batches.create(requests=api_requests)
        except Exception as exc:
            return json.dumps({
                "error": "Batch submission failed: %s" % exc,
                "task_id": task_id,
            })

        # Create BatchStore record
        create_result = json.loads(batch_store.create_batch(
            task_id=task_id,
            model=model,
            manifest=manifest,
            batch_api_id=batch.id,
            expires_at=batch.expires_at.isoformat() if batch.expires_at else "",
            estimated_input_tokens=total_est_tokens,
        ))
        batch_id = create_result["batch_id"]

        # Update linked task
        task_store.update_task(
            task_id=task_id,
            status="blocked",
            notes="Batch submitted: %d requests, model: %s, batch API ID: %s"
                  % (len(requests), model, batch.id),
        )

        # Audit log (feeds into session budget tracking)
        if audit is not None:
            audit.log_batch_submit(
                batch_id=batch_id, request_count=len(requests),
                model=model, estimated_tokens=total_est_tokens,
            )

        result = {
            "status": "submitted",
            "batch_id": batch_id,
            "batch_api_id": batch.id,
            "task_id": task_id,
            "request_count": len(requests),
            "model": model,
            "expires_at": batch.expires_at.isoformat() if batch.expires_at else "",
            "estimated_input_tokens": total_est_tokens,
        }
        if warnings:
            result["warnings"] = warnings
        return json.dumps(result)

    return _handler
