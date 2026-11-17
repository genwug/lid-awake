import AppKit

let size = 1024
let image = NSImage(size: NSSize(width: size, height: size), flipped: false) { rect in
    NSColor(srgbRed: 0.145, green: 0.118, blue: 0.090, alpha: 1).setFill()
    NSBezierPath(rect: rect).fill()

    let lid = NSBezierPath(roundedRect: NSRect(x: 176, y: 318, width: 672, height: 470), xRadius: 64, yRadius: 64)
    NSColor(srgbRed: 0.965, green: 0.945, blue: 0.910, alpha: 1).setFill()
    lid.fill()

    let screen = NSBezierPath(roundedRect: NSRect(x: 230, y: 390, width: 564, height: 330), xRadius: 28, yRadius: 28)
    NSColor(srgbRed: 0.894, green: 0.867, blue: 0.816, alpha: 1).setFill()
    screen.fill()

    let knobTrack = NSBezierPath(roundedRect: NSRect(x: 392, y: 500, width: 240, height: 120), xRadius: 60, yRadius: 60)
    NSColor(srgbRed: 0.545, green: 0.667, blue: 0.580, alpha: 1).setFill()
    knobTrack.fill()
    let knob = NSBezierPath(ovalIn: NSRect(x: 408, y: 514, width: 92, height: 92))
    NSColor(srgbRed: 0.996, green: 0.992, blue: 0.973, alpha: 1).setFill()
    knob.fill()

    let base = NSBezierPath(roundedRect: NSRect(x: 128, y: 214, width: 768, height: 92), xRadius: 32, yRadius: 32)
    NSColor(srgbRed: 0.616, green: 0.239, blue: 0.086, alpha: 1).setFill()
    base.fill()
    return true
}

guard CommandLine.arguments.count > 1,
      let tiff = image.tiffRepresentation,
      let rep = NSBitmapImageRep(data: tiff),
      let png = rep.representation(using: .png, properties: [:]) else {
    fputs("failed to draw the icon\n", stderr)
    exit(1)
}
try png.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
