# The mock DMS is a dumb system of record; PitLine (agent + dashboard) is the ops layer above it

VaultDrive's own pitch is "the operational layer that sits above the DMS
and runs the store instead of it." Rather than just renaming the old demo's
mock CRM to "DMS" and calling it done, PitLine's voice agent and ops
dashboard together play the role VaultDrive itself plays in production: the
DMS holds data and enforces nothing, PitLine's agent is the thing that
takes action against it, and the dashboard is the operational view a human
would actually work from — both reaching the DMS only through its API,
never touching its storage directly.

The alternative — a single service where "DMS" is just a vocabulary label
over the same CRM code — would be less work, but it stops being a
demonstration of VaultDrive's actual architectural thesis and becomes only
a demonstration of the underlying voice pipeline. The extra surface (a
second frontend, a second deployable) is deliberate: it's what makes the
demo an instance of VaultDrive's own claim rather than a generic voice-bot
example that happens to be about cars.
