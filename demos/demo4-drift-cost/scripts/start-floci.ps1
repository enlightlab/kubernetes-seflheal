# Start Floci local AWS emulator + Floci UI for Demo 4
#Requires -Version 5.1
$FlociDir = Join-Path (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))) "floci"
& (Join-Path $FlociDir "start-floci-stack.ps1")
