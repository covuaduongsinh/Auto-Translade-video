# Launcher: chay pipeline long tieng Viet o che do TUONG TAC (hien menu chon giong + log)
# Cach dung: double-click, hoac chuot phai -> Run with PowerShell
# Duong dan video doc tu last_video.txt (tranh loi ma hoa khi nhung tieng Viet vao .ps1)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# ffmpeg vao PATH (winget Gyan.FFmpeg)
$ffbin = "C:\Users\duongsinh\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.1-full_build\bin"
if (Test-Path $ffbin) { $env:PATH = "$ffbin;$env:PATH" }
$env:PYTHONIOENCODING = "utf-8"
chcp 65001 > $null

# --- Doi font console sang Consolas de hien thi tieng Viet co dau ---
$fontCode = @"
using System;
using System.Runtime.InteropServices;
public static class ConFont {
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
  public struct CONSOLE_FONT_INFO_EX {
    public uint cbSize; public uint nFont;
    public short FontWidth; public short FontHeight;
    public int FontFamily; public int FontWeight;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string FaceName;
  }
  [DllImport("kernel32.dll", SetLastError=true)] public static extern IntPtr GetStdHandle(int n);
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool SetCurrentConsoleFontEx(IntPtr h, bool max, ref CONSOLE_FONT_INFO_EX f);
  public static void Use(string face, short size) {
    IntPtr h = GetStdHandle(-11);
    var f = new CONSOLE_FONT_INFO_EX();
    f.cbSize = (uint)Marshal.SizeOf(typeof(CONSOLE_FONT_INFO_EX));
    f.FontFamily = 54; f.FontWeight = 400;
    f.FontWidth = 0; f.FontHeight = size; f.FaceName = face;
    SetCurrentConsoleFontEx(h, false, ref f);
  }
}
"@
try { Add-Type -TypeDefinition $fontCode; [ConFont]::Use("Consolas", 18) } catch { }
$OutputEncoding = [System.Text.Encoding]::UTF8

$video   = (Get-Content -Path (Join-Path $PSScriptRoot "last_video.txt") -Encoding UTF8 -Raw).Trim()
$workdir = "output\VN\20260608145124_vi"

Write-Host "============ AUTO-TRANSLATE VIDEO -- GIAO DIEN TUONG TAC ============" -ForegroundColor Cyan
Write-Host "Phan mem se hien MENU CHON GIONG. Ban go 1 (Nam) hoac 2 (Nu) roi Enter." -ForegroundColor Yellow
Write-Host "Sau do log tung STEP hien ra. (ASR bao loi 401 vi chua co key Azure.)"   -ForegroundColor Yellow
Write-Host "===================================================================="    -ForegroundColor Cyan
Write-Host ""

python pipeline_vi.py --resume $workdir --file $video

Write-Host ""
Write-Host "============ KET THUC. Nhan Enter de dong cua so. ============" -ForegroundColor Cyan
Read-Host
