"""Tool: submit_batch -- submit independent requests for async 50% cost processing."""
import json
import logging

LOG = logging.getLogger("daisy")

NAME = "submit_batch"
DESCRIPTION = (
    "Submit independent Claude API requests for async processing at 50% cost. "
    "Batch prompts have NO tool access -- include all pre-extracted data in the prompt. "
    "Preprocess with run_command/run_python first to minimize tokens. "
    "Automatically creates/updates a task to track the batch."
)
INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "task_id": {
            "type": "string",
            "description": "Link to existing task (optional -- creates new task if omitted)",
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
                "additionalProperties": False,
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "Human-readable label (e.g. 'block_A timing')",
                    },
                    "system": {
                        "type": "string",
                        "description": "System prompt (keep minimal -- default provided)",
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
            "description": "List of batch requests (minimum 1). Each prompt should contain PRE-EXTRACTED data.",
        },
    },
    "required": ["requests"],
}

_DEFAULT_BATCH_SYSTEM = (
    "You are an EDA analysis assistant. Be concise and data-driven. "
    "Respond with structured analysis: findings, metrics, and recommendations. "
    "Use markdown tables for numerical comparisons. Do not use tool calls."
)
_TOKEN_WARN_THRESHOLD = 8000  # ~32KB of text


def make_handler(batch_store=None, task_store=None, config=None, **kwargs):
    import anthropic

    def _handler(requests, task_id=None, task_name=None, model=None):
        if not requests:
            return json.dumps({"error": "requests must contain at least one item"})
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
