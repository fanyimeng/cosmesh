"""The intentionally small v0.1 local document format."""

import hashlib
import json
import re
from datetime import date, datetime
from uuid import UUID, uuid4

import yaml

from .errors import Error

MAX_DOCUMENT = 1024 * 1024
KINDS = ("project", "task", "update")
PREFIX = {"project": "P", "task": "T", "update": "U"}
USER = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\$?$")
HEX = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


class StrictLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise Error("SCHEMA_INVALID", "YAML aliases are not supported.")
        self.depth = getattr(self, "depth", 0) + 1
        if self.depth > 20:
            raise Error("SCHEMA_INVALID", "YAML nesting exceeds 20 levels.")
        try:
            return super().compose_node(parent, index)
        finally:
            self.depth -= 1

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise Error("SCHEMA_INVALID", "YAML keys must be unique strings.")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def identifier(value):
    try:
        prefix, raw = value.split("-", 1)
        parsed = UUID(raw)
        valid = prefix in PREFIX.values() and parsed.version == 4 and str(parsed) == raw
    except (AttributeError, TypeError, ValueError):
        valid = False
    if not valid:
        raise Error("INPUT_INVALID", "Expected a complete P-/T-/U- UUIDv4 document ID.")
    return value


def new_id(kind):
    return PREFIX[kind] + "-" + str(uuid4())


def timestamp(timezone):
    return datetime.now(timezone).isoformat(timespec="microseconds")


def day(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise Error("INPUT_INVALID", "Dates must be YYYY-MM-DD strings.")
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError:
        raise Error("INPUT_INVALID", "Dates must be valid YYYY-MM-DD strings.")
    return value


def version(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate(doc):
    meta = doc["meta"]
    if meta.get('schema_version') == 3:
        from .knowledge_protocol import validate as validate_knowledge
        return validate_knowledge(doc)
    if meta.get("schema_version") == 2:
        from .thread_protocol import validate as validate_thread
        return validate_thread(doc)
    try:
        json.dumps(meta, allow_nan=False)
    except (TypeError, ValueError):
        raise Error("SCHEMA_INVALID", "Metadata must contain JSON-compatible values.")
    if type(meta.get("schema_version")) is not int or meta["schema_version"] != 1:
        raise Error("SCHEMA_UNSUPPORTED", "This client writes local schema version 1 only.")
    kind = meta.get("kind")
    if kind not in KINDS:
        raise Error("SCHEMA_INVALID", "Unknown document kind.")
    identifier(meta.get("id"))
    if not meta["id"].startswith(PREFIX[kind] + "-"):
        raise Error("SCHEMA_INVALID", "Document kind and ID prefix disagree.")
    if not isinstance(meta.get("title"), str) or not meta["title"].strip():
        raise Error("SCHEMA_INVALID", "Document title cannot be empty.")
    if not isinstance(meta.get("author"), str) or not USER.fullmatch(meta["author"]):
        raise Error("SCHEMA_INVALID", "Invalid author ID.")
    for key in ("created_at", "updated_at"):
        try:
            parsed = datetime.fromisoformat(meta[key])
            if parsed.tzinfo is None:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise Error("SCHEMA_INVALID", "Timestamps must include a timezone.")
    if meta.get("lifecycle") not in ("active", "archived", "trash"):
        raise Error("SCHEMA_INVALID", "Invalid lifecycle state.")
    for key in ("due", "followup"):
        day(meta.get(key))
    for key in ("project", "task", "reply_to", "supersedes", "previous"):
        if meta.get(key) is not None:
            identifier(meta[key])
    for key in ("sources", "converted_to"):
        values = meta.get(key, [])
        if not isinstance(values, list) or not all(isinstance(x, str) for x in values):
            raise Error("SCHEMA_INVALID", key + " must be a list of reference strings.")
    if not isinstance(doc.get("body"), str):
        raise Error("SCHEMA_INVALID", "Document body must be text.")


def encode(doc):
    validate(doc)
    result = "---\n" + yaml.safe_dump(doc["meta"], allow_unicode=True, sort_keys=doc['meta'].get('schema_version')==3) + "---\n\n" + doc["body"].rstrip() + "\n"
    if len(result.encode("utf-8")) > MAX_DOCUMENT:
        raise Error("INPUT_TOO_LARGE", "A document cannot exceed 1 MiB.")
    return result


def decode(text, filename=None):
    if len(text.encode("utf-8")) > MAX_DOCUMENT:
        raise Error("INPUT_TOO_LARGE", "A document cannot exceed 1 MiB.")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise Error("SCHEMA_INVALID", "Missing YAML frontmatter.")
    end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        raise Error("SCHEMA_INVALID", "Unclosed YAML frontmatter.")
    try:
        meta = yaml.load("".join(lines[1:end]), Loader=StrictLoader)
    except yaml.YAMLError as exc:
        raise Error("SCHEMA_INVALID", "Invalid YAML frontmatter: " + str(exc))
    if not isinstance(meta, dict):
        raise Error("SCHEMA_INVALID", "Frontmatter must be a mapping.")
    doc = {"meta": meta, "body": "".join(lines[end + 1:]).strip("\n")}
    validate(doc)
    if filename is not None and filename != meta["id"] + ".md":
        raise Error("SCHEMA_INVALID", "Filename and document ID disagree.")
    return doc


def public(doc):
    return dict(doc["meta"], body=doc["body"], version=version(encode(doc)))
