# Show the MetaTrader 5 terminal window. The agent runs it hidden; stop the hidden instance, then
# open MT5 from its icon. The agent reattaches (and hides a fresh instance) on its next start.
Get-Process terminal64 -ErrorAction SilentlyContinue | Stop-Process
Start-Sleep -Seconds 2
$exe = "C:\Program Files\MetaTrader 5\terminal64.exe"
if (Test-Path $exe) { Start-Process $exe } else { Write-Host "Open MT5 from its desktop icon." }
