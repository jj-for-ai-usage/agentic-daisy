"""Tool: get_batch_results -- retrieve results from a completed batch."""
import json

NAME = "get_batch_results"
DESCRIPTION = (
    "Retrieve results from a completed batch. Fetch all or a specific request by "
    "index. Use summary_only=true for an overview before drilling into details."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "batch_id": {
            "type": "string",
            "description": "Batch ID (e.g. 'batch_20260404_001')",
        },
        "index": {
            "type": "integer",
            "description": "0-based index for a single result (omit for all)",
        },
        "summary_only": {
            "type": "boolean",
            "description": "Truncate each result to 200 chars (default: false)",
        },
    },
    "required": ["batch_id"],
}


def make_handler(batch_store=None, **kwargs):
    def _handler(batch_id, index=None, summary_only=False):
        batch_data = json.loads(batch_store.get_batch(batch_id))
        if "error" in batch_data:
            return json.dumps(batch_data)

        batch = batch_data["batch"]
        if batch["status"] not in ("results_retrieved", "ended"):
            return json.dumps({
                "error": "Batch '%s' status is '%s'. Call check_batch first."
                         % (batch_id, batch["status"]),
            })

        manifest = batch.get("manifest", [])

        # Determine which results to fetch
        if index is not None:
            if index < 0 or index >= len(manifest):
                return json.dumps({
                    "error": "Index %d out of range (0-%d)" % (index, len(manifest) - 1),
                })
            entries = [(index, manifest[index])]
        else:
            entries = list(enumerate(manifest))

        results = []
        for i, entry in entries:
            custom_id = entry["custom_id"]
            label = entry.get("label", "request_%d" % i)
            raw = batch_store.read_result(batch_id, custom_id)
            try:
                data = json.loads(raw)
                text = data.get("text", "")
                status = data.get("status", "unknown")
            except (json.JSONDecodeError, TypeError):
                text = ""
                status = "read_error"

            if summary_only and len(text) > 200:
                text = text[:200] + "..."

            results.append({
                "index": i,
                "label": label,
                "status": status,
                "text": text,
            })

        return json.dumps({
            "batch_id": batch_id,
            "count": len(results),
            "results": results,
        })

    return _handler
