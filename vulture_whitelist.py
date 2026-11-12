"""Names vulture cannot see being used, because only a framework reads them.

Convention (min_confidence 60 reports unused class attributes):
- Add an entry only for a name a framework uses implicitly: an ORM column read via
  the mapper, a pydantic field read only during serialization, and the like.
- One entry per name, as `_.name`, with a comment naming the file and the reader:
      _.created_at  # app/modules/auth/models.py: SQLAlchemy column, read by the ORM
- Suggestions can be generated with `uv run vulture --make-whitelist`; copy only
  entries that meet the rule above. Dead functions and classes are deleted, not listed.
- vulture matches by name, so an entry whitelists that name everywhere: prefer a
  distinctive field name, and remove the entry when the field goes.

This file is parsed by vulture only; it is never imported or executed.
"""

_.ryuk_disabled  # tests/conftest.py: testcontainers config attribute, read when a container starts
_.it_parent_id  # tests/integration/test_model_base.py: SQLAlchemy column, read by the ORM
