"""archive-harness: source-agnostic overwrite detector.

The harness knows nothing about what any record means. It stores raw payloads,
indexes (record_key, value_hash) pairs, and measures how the key set changes
between snapshots.
"""

__version__ = "0.1.0"
