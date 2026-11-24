# MongoDB document schema

To be completed in Phase 3.

Planned design: one document per solar system per day, with the hourly readings
embedded as an array.

- Why embed: readings are always read together with their day and system, and the
  array is bounded (24 entries), so embedding avoids a join.
- Why reference: (explain any referenced collections here)
