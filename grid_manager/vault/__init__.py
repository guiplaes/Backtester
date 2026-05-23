"""Vault DCA System — gestió d'inventari de actius/USDT fora dels bots actius.

Components:
    inventory   — CRUD + MTM de la taula vault_inventory
    injection   — consume injection_queue (cron 60s)
    harvester   — extreu profits dels bots i afegeix a inv_usdt (cron diari)

NOTA: closer/relauncher/funding engine es construeixen en Fase 2 quan llegim
monitor.py i bot_operations.py més a fons. Aquest paquet només cobreix Fase 1
(tracking passiu paral·lel, no toca bots reals).
"""
