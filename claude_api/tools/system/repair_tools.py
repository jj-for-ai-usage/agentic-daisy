"""Tool: repair_tools — scan, diagnose, and auto-fix custom tool definitions."""
import json
import os
import re
import shutil

NAME = "repair_tools"
DESCRIPTION = (
    "Scan custom tool directories and diagnose/fix problems with .py tool files. "
    "Default: dry-run report. Set fix=true to apply safe auto-fixes."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "fix": {
            "type": "boolean",
            "description": "Apply safe auto-fixes (default: false, dry-run only)",
        },
        "delete_unfixable": {
            "type": "boolean",
            "description": "Delete tools that cannot be auto-fixed (requires fix=true)",
        },
    },
}

_REQUIRED_ATTRS = ("NAME", "DESCRIPTION", "INPUT_SCHEMA", "handler")


def _add_type_to_schema(source):
    """Insert 'type': 'object' into INPUT_SCHEMA in the source text."""
    return re.sub(
        r'(INPUT_SCHEMA\s*=\s*\{)',
        r'\1\n  "type": "object",',
        source,
        count=1,
    )


def make_handler(config=None, registry=None, **kwargs):
    from claude_api.config import USER_TOOLS_DIR, DEFAULT_CUSTOM_TOOLS_DIR
    from claude_api.custom_tools import CustomToolLoader

    def _handler(fix=False, delete_unfixable=False):
        dirs = [USER_TOOLS_DIR, DEFAULT_CUSTOM_TOOLS_DIR]
        loader = CustomToolLoader(dirs)
        results = []

        for d in dirs:
            if not os.path.isdir(d):
                continue
            for fname in sorted(os.listdir(d)):
                if not fname.endswith(".py") or fname.startswith("_"):
                    continue
                path = os.path.join(d, fname)
                entry = {
                    "file": path,
                    "issues": [],
                    "fixes_applied": [],
                    "status": "ok",
                }

                # Phase 1: Try to import
                try:
                    mod = loader._import_file(path)
                except SyntaxError as exc:
                    entry["issues"].append("SyntaxError: %s" % exc)
                    entry["status"] = "unfixable"
                    if fix and delete_unfixable:
                        bak_path = path + ".bak"
                        try:
                            shutil.copy2(path, bak_path)
                            entry["backup"] = bak_path
                        except OSError:
                            pass
                        os.remove(path)
                        entry["status"] = "deleted"
                    results.append(entry)
                    continue
                except Exception as exc:
                    entry["issues"].append("ImportError: %s" % exc)
                    entry["status"] = "unfixable"
                    if fix and delete_unfixable:
                        bak_path = path + ".bak"
                        try:
                            shutil.copy2(path, bak_path)
                            entry["backup"] = bak_path
                        except OSError:
                            pass
                        os.remove(path)
                        entry["status"] = "deleted"
                    results.append(entry)
                    continue

                # Phase 2: Check required attributes
                needs_rewrite = False
                for attr in _REQUIRED_ATTRS:
                    if not hasattr(mod, attr):
                        entry["issues"].append("Missing attribute: %s" % attr)
                        entry["status"] = "unfixable"

                # Phase 3: Validate INPUT_SCHEMA
                if hasattr(mod, "INPUT_SCHEMA"):
                    schema = mod.INPUT_SCHEMA
                    if not isinstance(schema, dict):
                        entry["issues"].append("INPUT_SCHEMA is not a dict")
                        entry["status"] = "unfixable"
                    elif "type" not in schema:
                        entry["issues"].append("INPUT_SCHEMA missing 'type' key")
                        if fix:
                            try:
                                with open(path, "r") as f:
                                    source = f.read()
                                source = _add_type_to_schema(source)
                                with open(path, "w") as f:
                                    f.write(source)
                                needs_rewrite = True
                                entry["fixes_applied"].append(
                                    "Added 'type': 'object' to INPUT_SCHEMA"
                                )
                            except Exception as exc:
                                entry["issues"].append(
                                    "Fix failed: %s" % exc
                                )

                # Phase 4: Validate handler is callable
                if hasattr(mod, "handler") and not callable(mod.handler):
                    entry["issues"].append("handler exists but is not callable")
                    entry["status"] = "unfixable"

                # Phase 5: Re-import and re-register after fixes
                if fix and needs_rewrite:
                    try:
                        mod = loader._import_file(path)
                        if registry and all(
                            hasattr(mod, a) for a in _REQUIRED_ATTRS
                        ):
                            registry.register(
                                mod.NAME, mod.DESCRIPTION,
                                mod.INPUT_SCHEMA, mod.handler,
                            )
                            entry["status"] = "fixed_and_registered"
                    except Exception as exc:
                        entry["issues"].append(
                            "Re-import after fix failed: %s" % exc
                        )
                        entry["status"] = "partially_fixed"

                # Determine final status
                if not entry["issues"]:
                    entry["status"] = "ok"
                elif (
                    entry["status"] not in ("unfixable", "fixed_and_registered", "partially_fixed")
                ):
                    entry["status"] = "needs_manual_fix"

                # Delete unfixable if requested
                if (
                    fix and delete_unfixable
                    and entry["status"] == "unfixable"
                    and os.path.exists(path)
                ):
                    bak_path = path + ".bak"
                    try:
                        shutil.copy2(path, bak_path)
                        entry["backup"] = bak_path
                    except OSError:
                        pass
                    os.remove(path)
                    entry["status"] = "deleted"

                results.append(entry)

        summary = {
            "total_scanned": len(results),
            "ok": sum(1 for r in results if r["status"] == "ok"),
            "fixed": sum(1 for r in results if "fixed" in r["status"]),
            "needs_manual_fix": sum(
                1 for r in results
                if r["status"] in ("unfixable", "needs_manual_fix")
            ),
            "deleted": sum(1 for r in results if r["status"] == "deleted"),
            "mode": "fix" if fix else "dry_run",
        }
        return json.dumps({"summary": summary, "tools": results})

    return _handler
