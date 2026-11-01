# Show the MetaTrader 5 terminal window. The agent runs it hidden; stop the hidden instance, then
# open MT5 from its icon. The agent reattaches (and hides a fresh instance) on its next start.
# Stopping the terminal drops its connection while the agent may hold open positions, so the
# script asks first. Pass -Force to skip the question.
param([switch]$Force)
$running = Get-Process terminal64 -ErrorAction SilentlyContinue
if ($running -and -not $Force) {
    Write-Host "Le terminal MT5 va etre ferme puis relance visible."
    Write-Host "Verifiez d'abord qu'aucune position n'est ouverte (stops en place cote broker)."
    $answer = Read-Host "Continuer ? (o/N)"
    if ($answer -notmatch '^(o|oui|y|yes)$') { Write-Host "Annule."; exit 1 }
}
if ($running) { $running | Stop-Process; Start-Sleep -Seconds 2 }
$exe = "C:\Program Files\MetaTrader 5\terminal64.exe"
if (Test-Path $exe) { Start-Process $exe } else { Write-Host "Open MT5 from its desktop icon." }
