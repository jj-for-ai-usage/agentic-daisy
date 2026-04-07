"""Tool: check_batch -- poll batch status and auto-retrieve results."""
import json
import logging

LOG = logging.getLogger("daisy")

NAME = "check_batch"
DESCRIPTION = (
    "Check status of batch jobs. If complete, automatically downloads and saves "
    "results. Call with no arguments to check ALL pending batches."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "batch_id": {
            "type": "string",
            "description": "Specific batch ID (omit to check all pending batches)",
        },
    },
}


def make_handler(batch_store=None, task_store=None, config=None, **kwargs):
    import anthropic

    def _handler(batch_id=None):
        client = anthropic.Anthropic(api_key=config.api_key)

        # Determine which batches to check
        if batch_id:
            batch_data = json.loads(batch_store.get_batch(batch_id))
            if "error" in batch_data:
                return json.dumps(batch_data)
            batches_to_check = [batch_data["batch"]]
        else:
            batches_to_check = batch_store.get_pending_batches()
            if not batches_to_check:
                return json.dumps({"status": "no_pending_batches", "batches": []})

        results = []
        for batch in batches_to_check:
            bid = batch["id"]
            api_id = batch["batch_api_id"]

            try:
                api_batch = client.messages.batches.retrieve(api_id)
            except Exception as exc:
                results.append({
                    "batch_id": bid,
                    "status": "error",
                    "error": "Failed to retrieve: %s" % exc,
                })
                continue

            # Update request counts
            counts = None
            if api_batch.request_counts:
                counts = {
                    "processing": api_batch.request_counts.processing,
                    "succeeded": api_batch.request_counts.succeeded,
                    "errored": api_batch.request_counts.errored,
                    "canceled": api_batch.request_counts.canceled,
                    "expired": api_batch.request_counts.expired,
                }

            batch_update = {"request_counts": counts}

            if api_batch.processing_status == "ended":
                batch_update["ended_at"] = (
                    api_batch.ended_at.isoformat() if api_batch.ended_at else None
                )

                # Stream and save results
                total_in = 0
                total_out = 0
                succeeded = 0
                errored = 0

                try:
                    for resp in client.messages.batches.results(api_id):
                        custom_id = resp.custom_id
                        if resp.result.type == "succeeded":
                            # Extract text content only
                            text_parts = []
                            for block in resp.result.message.content:
                                if hasattr(block, "text"):
                                    text_parts.append(block.text)
                            text = "\n".join(text_parts)
                            total_in += resp.result.message.usage.input_tokens
                            total_out += resp.result.message.usage.output_tokens
                            batch_store.save_result(bid, custom_id, text, "succeeded")
                            succeeded += 1
                        elif resp.result.type == "errored":
                            error_text = str(resp.result.error) if hasattr(resp.result, "error") else "Unknown error"
                            batch_store.save_result(bid, custom_id, error_text, "errored")
                            errored += 1
                        else:
                            # canceled or expired
                            batch_store.save_result(
                                bid, custom_id,
                                "Request %s" % resp.result.type,
                                resp.result.type,
                            )

                    batch_update["status"] = "results_retrieved"
                    batch_update["actual_input_tokens"] = total_in
                    batch_update["actual_output_tokens"] = total_out

                except Exception as exc:
                    LOG.warning("Failed to stream results for %s: %s", bid, exc)
                    batch_update["status"] = "ended"
                    results.append({
                        "batch_id": bid,
                        "status": "error_streaming",
                        "error": "Results streaming failed: %s. Retry with check_batch." % exc,
                    })
                    batch_store.update_batch(bid, **batch_update)
                    continue

                batch_store.update_batch(bid, **batch_update)

                # Update linked task
                if batch.get("task_id"):
                    task_store.update_task(
                        task_id=batch["task_id"],
                        status="active",
                        notes="Batch %s complete: %d succeeded, %d errored. "
                              "Use get_batch_results('%s') to view."
                              % (bid, succeeded, errored, bid),
                    )

                results.append({
                    "batch_id": bid,
                    "status": "results_retrieved",
                    "succeeded": succeeded,
                    "errored": errored,
                    "total_input_tokens": total_in,
                    "total_output_tokens": total_out,
                })
            else:
                # Still processing
                batch_store.update_batch(bid, **batch_update)
                processing = counts.get("processing", 0) if counts else "?"
                results.append({
                    "batch_id": bid,
                    "status": api_batch.processing_status,
                    "processing": processing,
                })

        return json.dumps({"batches": results})

    return _handler
