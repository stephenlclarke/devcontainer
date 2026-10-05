# C02 events terminate during a conflicting inventory observation

The failed bf02d4959b8cbcd970a4d8fd6e3e8150c82b3167 campaign's Q C02 starts its three services, but the Docker events frontend exits with unexpected HTTP EOF and the reference CLI produces no final JSON. At 16:46:59 the Engine server records `Container identity changed during CLI inventory` as a failed managed stream.

The runtime compares a CLI row with precise native identity across separate reads. The event poller currently treats the resulting explicit conflict as terminal for every subscriber. The evidence confirms that failure chain; it does not identify which compared field changed. Preserve all identity checks and the original failed campaign.
