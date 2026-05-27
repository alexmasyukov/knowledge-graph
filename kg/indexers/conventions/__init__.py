"""Project-convention rules — the semantic layer over the structural extractors.

A convention encodes a project-wide pattern (e.g. <PageDataProvider> binds a
data-loader hook to its enclosing Component via a JSX attribute, not a direct
function call). Each convention lives in its own module so adding a new one
is one PR, one file.

Conventions run AFTER all structural extractors and see the already-populated
graph. They emit additional nodes/edges via the same IngestResult shape.
"""
