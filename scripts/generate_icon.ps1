Add-Type -AssemblyName System.Drawing
$size = 256
$bmp = New-Object System.Drawing.Bitmap $size, $size
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality

# Clear with transparent
$g.Clear([System.Drawing.Color]::Transparent)

# Deep black squircle background (CapCut style)
$rect = New-Object System.Drawing.Rectangle 12, 12, 232, 232
$bgBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 10, 10, 12))
$path = New-Object System.Drawing.Drawing2D.GraphicsPath
$d = 56
$r = New-Object System.Drawing.Rectangle $rect.X, $rect.Y, $d, $d
$path.AddArc($r, 180, 90)
$r.X = $rect.Right - $d
$path.AddArc($r, 270, 90)
$r.Y = $rect.Bottom - $d
$path.AddArc($r, 0, 90)
$r.X = $rect.Left
$path.AddArc($r, 90, 90)
$path.CloseFigure()
$g.FillPath($bgBrush, $path)

# Subtle silver rim
$rimPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(60, 255, 255, 255), 1.5)
$g.DrawPath($rimPen, $path)

# Target circle (radar)
$circlePen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(35, 255, 255, 255), 1.5)
$circlePen.DashStyle = [System.Drawing.Drawing2D.DashStyle]::Dash
$g.DrawEllipse($circlePen, 78, 78, 100, 100)

# Precision ticks
$tickPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(255, 255, 255, 255), 2.0)
$g.DrawLine($tickPen, 128, 40, 128, 54)
$g.DrawLine($tickPen, 128, 202, 128, 216)
$g.DrawLine($tickPen, 40, 128, 54, 128)
$g.DrawLine($tickPen, 202, 128, 216, 128)

# Bold C ribbon (pure white)
$cPen = New-Object System.Drawing.Pen([System.Drawing.Color]::White, 14.0)
$cPen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
$cPen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
$g.DrawArc($cPen, 72, 72, 112, 112, 50, 260)

# Play triangle (pure white)
$playPts = @(
    (New-Object System.Drawing.PointF 116, 90),
    (New-Object System.Drawing.PointF 116, 166),
    (New-Object System.Drawing.PointF 178, 128)
)
$whiteBrush = [System.Drawing.Brushes]::White
$g.FillPolygon($whiteBrush, $playPts)

# Black gap outline for Play triangle
$gapPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(255, 10, 10, 12), 3.0)
$g.DrawPolygon($gapPen, $playPts)

# Focal dot at tip of Play triangle
$dotBrush = [System.Drawing.Brushes]::White
$g.FillEllipse($dotBrush, 172, 122, 12, 12)
$innerDot = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 10, 10, 12))
$g.FillEllipse($innerDot, 176, 126, 4, 4)

$g.Dispose()

# Save ICO
$hIcon = $bmp.GetHicon()
$icon = [System.Drawing.Icon]::FromHandle($hIcon)
$targetPath = "assets/icon.ico"
$fs = [System.IO.File]::Create($targetPath)
$icon.Save($fs)
$fs.Close()
$icon.Dispose()
$bmp.Dispose()
Write-Host "Generated CapCut B&W icon at $targetPath!"
