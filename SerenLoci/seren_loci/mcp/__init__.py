"""
seren_loci.mcp
═══════════════

Optional MCP server surface for SerenLoci. Only meaningful when the [mcp]
extras are installed (`pip install seren-loci[mcp]`); without those deps this
subpackage's modules fail to import and app.py's mount-attempt silently no-ops,
leaving SerenLoci in pure-HTTP mode.

This is the surface a connected model reaches the left brain through - set a
fact, get THE value for a key, search for the door when you don't know the key.
The tools call LociStore directly (not via an HTTP round-trip to ourselves)
since we're mounted INTO the same FastAPI app that owns the store. Less wire,
less latency, fewer failure modes.
"""
