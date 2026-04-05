"""Tool: create_tool — create a new custom tool .py file."""
import json
import os
import re
import textwrap

NAME = "create_tool"
DESCRIPTION = (
    "Create a new custom tool .py file. Saves to ~/.daisy/tools/ "
    "(persistent across sessions) by default, or .daisy/tools/ (project-level). "
    "The tool is registered immediately and available in the current session."
)
INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Tool name in snake_case (e.g. parse_timing_report)",
        },
        "description": {
            "type": "string",
            "description": "Tool description (shown to Claude when selecting tools)",
        },
        "input_schema": {
            "type": "object",
            "description": "JSON Schema for tool parameters (properties, required, etc.)",
        },
        "handler_code": {
            "type": "string",
            "description": (
                "Python code for the handler function body. Will be placed "
                "inside 'def handler(**kwargs):'. Use kwargs to access parameters. "
                "Must return a JSON string (use json.dumps)."
            ),
        },
        "location": {
            "type": "string",
            "enum": ["user", "project"],
            "description": "Where to save: 'user' (~/.daisy/tools/, default) or 'project' (.daisy/tools/)",
        },
    },
    "required": ["name", "description", "input_schema", "handler_code"],
}


def make_handler(config=None, registry=None, **kwargs):
    from claude_api.config import USER_TOOLS_DIR, DEFAULT_CUSTOM_TOOLS_DIR
    from claude_api.custom_tools import CustomToolLoader

    def _handler(name, description, input_schema, handler_code, location="user"):
        # Sanitize name
        safe_name = re.sub(r"[^a-z0-9_]", "_", name.lower())
        # Check for name collision with existing tools
        if registry is not None and registry.get(safe_name) is not None:
            return json.dumps({
                "status": "name_conflict",
                "name": safe_name,
                "error": "A tool named '%s' already exists. Choose a different name." % safe_name,
            })
        # Ensure input_schema has required "type" field for Anthropic API
        if not isinstance(input_schema, dict):
            input_schema = {"type": "object"}
        if "type" not in input_schema:
            input_schema["type"] = "object"
        # Resolve target directory
        if location == "project":
            target_dir = DEFAULT_CUSTOM_TOOLS_DIR
        else:
            target_dir = USER_TOOLS_DIR
        os.makedirs(target_dir, exist_ok=True)
        path = os.path.join(target_dir, safe_name + ".py")
        # Build .py file content
        schema_str = json.dumps(input_schema, indent=2)
        indented_code = textwrap.indent(handler_code.strip(), "    ")
        content = (
            '"""Custom tool: %s — %s"""\n'
            "import json\n"
            "\n"
            "NAME = %r\n"
            "DESCRIPTION = %r\n"
            "INPUT_SCHEMA = %s\n"
            "\n"
            "\n"
            "def handler(**kwargs):\n"
            "%s\n"
        ) % (safe_name, description, safe_name, description, schema_str, indented_code)
        # Pre-validate syntax before writing to disk
        try:
            compile(content, safe_name + ".py", "exec")
        except SyntaxError as exc:
            return json.dumps({
                "status": "syntax_error",
                "name": safe_name,
                "error": "Generated code has syntax error on line %s: %s" % (exc.lineno, exc.msg),
                "hint": "Fix the handler_code and try again. Check for unmatched quotes or invalid indentation.",
            })
        with open(path, "w") as f:
            f.write(content)
        # Dynamically import and register immediately
        try:
            loader = CustomToolLoader([target_dir])
            # Import just this one file
            mod = loader._import_file(path)
            if registry is not None:
                registry.register(mod.NAME, mod.DESCRIPTION,
                                  mod.INPUT_SCHEMA, mod.handler)
        except Exception as exc:
            # Remove broken file to avoid confusing state on next startup
            try:
                os.remove(path)
            except OSError:
                pass
            return json.dumps({
                "status": "import_failed",
                "name": safe_name,
                "error": "Code is syntactically valid but failed to import: %s" % exc,
                "hint": "Check that all imports are available in the environment.",
            })
        return json.dumps({
            "status": "created",
            "name": safe_name,
            "path": path,
            "location": location,
        })

    return _handler
