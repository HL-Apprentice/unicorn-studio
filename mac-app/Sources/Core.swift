// Unicorn Studio core: display files, resampling, GIF import, config, tiny MQTT 3.1.1 client, uploader.
// No secrets in source. Works with Pimoroni Cosmic (32x32), Galactic (53x11) and Stellar (16x16) Unicorn boards.
import Foundation
import Network
import ImageIO
import CoreGraphics
import Security

public let MAX_FRAMES = 64
public let BUILTIN_MODES = ["clock", "today", "agenda", "scroll_text", "fire", "rainbow", "supercomputer",
                            "starfield", "matrix", "plasma", "life"]

// ---------- boards / canvas sizes ----------
public struct BoardKind: Hashable, Identifiable {
    public let id: String; public let w: Int; public let h: Int
    public var label: String { "\(id.capitalized) \(w)×\(h)" }
    public static let cosmic = BoardKind(id: "cosmic", w: 32, h: 32)
    public static let galactic = BoardKind(id: "galactic", w: 53, h: 11)
    public static let stellar = BoardKind(id: "stellar", w: 16, h: 16)
    public static let all = [cosmic, galactic, stellar]
    public static func forSize(_ w: Int, _ h: Int) -> BoardKind { all.first { $0.w == w && $0.h == h } ?? BoardKind(id: "custom", w: w, h: h) }
}

public enum FitMode: String, CaseIterable, Identifiable { case fit, fill, stretch; public var id: String { rawValue } }

// Input limits (defence against huge or hostile files)
public enum Limits {
    public static let maxFileBytes = 32 * 1024 * 1024
    public static let maxSide = 256
    public static let maxFramesRead = 1000
    public static let maxImagePixels = 4096 * 4096
}

public struct Frame: Identifiable, Equatable {
    public var id = UUID()
    public var px: [UInt32]          // 0xRRGGBB, row-major, upright
    public var dur: Int              // ms
    public init(w: Int = 32, h: Int = 32, dur: Int = 200) { px = [UInt32](repeating: 0, count: w * h); self.dur = dur }
    public init(px: [UInt32], dur: Int) { self.px = px; self.dur = dur }
    public func rgb() -> Data {
        var d = Data(capacity: px.count * 3)
        for c in px { d.append(UInt8((c >> 16) & 255)); d.append(UInt8((c >> 8) & 255)); d.append(UInt8(c & 255)) }
        return d
    }
}

public struct Display {
    public var name: String; public var desc: String; public var w: Int; public var h: Int; public var frames: [Frame]
    public init(name: String, desc: String, w: Int, h: Int, frames: [Frame]) {
        self.name = name; self.desc = desc; self.w = w; self.h = h; self.frames = frames
    }
    /// Same animation at another size. Area-averaged (smooth when shrinking, crisp for exact multiples).
    public func resized(_ W: Int, _ H: Int, mode: FitMode = .fit) -> Display {
        if W == w && H == h { return self }
        return Display(name: name, desc: desc, w: W, h: H,
                       frames: frames.map { Frame(px: Resample.px($0.px, w, h, W, H, mode), dur: $0.dur) })
    }
}

public enum Resample {
    public static func px(_ src: [UInt32], _ sw: Int, _ sh: Int, _ W: Int, _ H: Int, _ mode: FitMode) -> [UInt32] {
        var out = [UInt32](repeating: 0, count: W * H)
        guard sw > 0, sh > 0, src.count == sw * sh else { return out }
        var sx = Double(W) / Double(sw), sy = Double(H) / Double(sh)
        switch mode {
        case .fit: let s = min(sx, sy); sx = s; sy = s
        case .fill: let s = max(sx, sy); sx = s; sy = s
        case .stretch: break
        }
        let ox = (Double(W) - Double(sw) * sx) / 2, oy = (Double(H) - Double(sh) * sy) / 2
        for y in 0..<H { for x in 0..<W {
            // source rectangle covered by this destination pixel
            let x0 = (Double(x) - ox) / sx, x1 = (Double(x + 1) - ox) / sx
            let y0 = (Double(y) - oy) / sy, y1 = (Double(y + 1) - oy) / sy
            if x1 <= 0 || y1 <= 0 || x0 >= Double(sw) || y0 >= Double(sh) { continue }
            var r = 0.0, g = 0.0, b = 0.0, wsum = 0.0, cover = 0.0
            let ix0 = max(0, Int(x0.rounded(.down))), ix1 = min(sw - 1, Int((x1 - 1e-9).rounded(.down)))
            let iy0 = max(0, Int(y0.rounded(.down))), iy1 = min(sh - 1, Int((y1 - 1e-9).rounded(.down)))
            if ix1 < ix0 || iy1 < iy0 { continue }
            for yy in iy0...iy1 { for xx in ix0...ix1 {
                let wx = min(x1, Double(xx + 1)) - max(x0, Double(xx)), wy = min(y1, Double(yy + 1)) - max(y0, Double(yy))
                let wgt = max(0, wx) * max(0, wy); if wgt <= 0 { continue }
                let c = src[yy * sw + xx]
                r += Double((c >> 16) & 255) * wgt; g += Double((c >> 8) & 255) * wgt; b += Double(c & 255) * wgt
                wsum += wgt
            } }
            cover = wsum / ((x1 - x0) * (y1 - y0))
            if wsum <= 0 || cover < 0.5 { continue }
            out[y * W + x] = UInt32(min(255, r / wsum + 0.5)) << 16 | UInt32(min(255, g / wsum + 0.5)) << 8 | UInt32(min(255, b / wsum + 0.5))
        } }
        return out
    }
}

func uerr(_ code: Int, _ msg: String) -> NSError { NSError(domain: "unicorn", code: code, userInfo: [NSLocalizedDescriptionKey: msg]) }

public enum DisplayIO {
    public static var libDir: URL {
        if let p = ProcessInfo.processInfo.environment["UNICORN_LIBRARY"], !p.isEmpty { return URL(fileURLWithPath: p) }
        return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Documents/Unicorn Displays")
    }
    /// First launch: copy the animations bundled in the app into an empty library.
    public static func seedLibraryIfEmpty() {
        guard list().isEmpty, let src = Bundle.main.resourceURL?.appendingPathComponent("Animations"),
              FileManager.default.fileExists(atPath: src.path) else { return }
        let fm = FileManager.default
        guard let items = try? fm.contentsOfDirectory(at: src, includingPropertiesForKeys: nil) else { return }
        for u in items { try? fm.copyItem(at: u, to: libDir.appendingPathComponent(u.lastPathComponent)) }
    }
    public static func boardName(_ s: String) -> String {
        let ok = Set("abcdefghijklmnopqrstuvwxyz0123456789_-")
        let n = String(s.lowercased().map { ok.contains($0) ? $0 : "_" }.prefix(24))
        return n.isEmpty ? "display" : n
    }
    static func hexBytes(_ s: String) -> [UInt8] {
        var out = [UInt8](); out.reserveCapacity(s.utf8.count / 2)
        var hi: UInt8? = nil
        for c in s.utf8 {
            let v: UInt8
            switch c { case 48...57: v = c - 48; case 65...70: v = c - 55; case 97...102: v = c - 87; default: continue }
            if let h = hi { out.append(h << 4 | v); hi = nil } else { hi = v }
        }
        return out
    }
    static func rgbAt(_ b: [UInt8], _ i: Int) -> UInt32 {
        let r = UInt32(b[i * 3]), g = UInt32(b[i * 3 + 1]), bl = UInt32(b[i * 3 + 2])
        return (r << 16) | (g << 8) | bl
    }
    static func pixelBytes(_ v: Any) -> [UInt8] {
        if let s = v as? String { return hexBytes(s) }
        if let a = v as? [String] { return hexBytes(a.map { String($0.replacingOccurrences(of: "#", with: "").prefix(6)).leftPad(6) }.joined()) }
        if let a = v as? [Int] { return a.flatMap { [UInt8(($0 >> 16) & 255), UInt8(($0 >> 8) & 255), UInt8($0 & 255)] } }
        return []
    }
    public static func list() -> [URL] {
        try? FileManager.default.createDirectory(at: libDir, withIntermediateDirectories: true)
        var out = [URL]()
        if let en = FileManager.default.enumerator(at: libDir, includingPropertiesForKeys: nil) {
            for case let u as URL in en where u.pathExtension.lowercased() == "json" && !rel(u).hasPrefix("Playlists/") { out.append(u) }
        }
        return out.sorted { rel($0).localizedStandardCompare(rel($1)) == .orderedAscending }
    }
    public static func rel(_ u: URL) -> String {
        let b = libDir.standardizedFileURL.path + "/"
        let p = u.standardizedFileURL.path
        return p.hasPrefix(b) ? String(p.dropFirst(b.count)) : u.lastPathComponent
    }
    static func readLimited(_ url: URL) throws -> Data {
        let size = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int) ?? 0
        if size > Limits.maxFileBytes { throw uerr(4, "file is larger than \(Limits.maxFileBytes / 1048576) MB") }
        return try Data(contentsOf: url)
    }
    /// Loads a display at its native size (width/height in the file; 32x32 when absent).
    public static func load(_ url: URL) throws -> Display {
        let obj = try JSONSerialization.jsonObject(with: readLimited(url))
        guard let d = obj as? [String: Any], let fr = d["frames"] as? [[String: Any]] else { throw uerr(1, "not a display JSON (no frames)") }
        if fr.count > Limits.maxFramesRead { throw uerr(5, "too many frames (\(fr.count) > \(Limits.maxFramesRead))") }
        var w = d["width"] as? Int ?? 32, h = d["height"] as? Int ?? 32
        if let rows = fr.first?["rows"] as? [String], d["width"] == nil { h = rows.count; w = rows.map { $0.count }.max() ?? 32 }
        guard (1...Limits.maxSide).contains(w), (1...Limits.maxSide).contains(h) else { throw uerr(6, "size \(w)x\(h) not supported (max \(Limits.maxSide))") }
        var pal = [Character: UInt32]()
        if let p = d["palette"] as? [String: String] {
            for (k, v) in p { if let c = k.first, let x = UInt32(v.replacingOccurrences(of: "#", with: "").prefix(6), radix: 16) { pal[c] = x } }
        }
        var frames = [Frame]()
        for f in fr {
            var px = [UInt32](repeating: 0, count: w * h)
            if let rows = f["rows"] as? [String] {
                for (y, row) in rows.prefix(h).enumerated() { for (x, ch) in row.prefix(w).enumerated() { px[y * w + x] = pal[ch] ?? 0 } }
            } else if let v = f["pixels"] ?? f["hex"] {
                let b = pixelBytes(v)
                if b.count >= w * h * 3 {
                    for i in 0..<(w * h) { px[i] = rgbAt(b, i) }
                } else if b.count == 3072 {     // legacy: 32x32 pixels with a wrong width/height header
                    let src: [UInt32] = (0..<1024).map { rgbAt(b, $0) }
                    px = Resample.px(src, 32, 32, w, h, .stretch)
                }
            }
            frames.append(Frame(px: px, dur: max(20, min(60000, f["duration_ms"] as? Int ?? 200))))
        }
        if frames.isEmpty { frames = [Frame(w: w, h: h)] }
        let name = d["name"] as? String ?? url.deletingPathExtension().lastPathComponent
        return Display(name: name, desc: d["description"] as? String ?? "", w: w, h: h, frames: frames)
    }
    public static func save(_ disp: Display, to url: URL) throws {
        let frames: [[String: Any]] = disp.frames.map { f in
            ["duration_ms": f.dur, "pixels": f.px.map { String(format: "%06x", $0 & 0xFFFFFF) }.joined()]
        }
        let d: [String: Any] = ["format": "unicorn-display", "version": 1, "name": disp.name,
                                "description": disp.desc, "width": disp.w, "height": disp.h, "frames": frames]
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONSerialization.data(withJSONObject: d, options: [.prettyPrinted, .sortedKeys]).write(to: url, options: .atomic)
    }
    /// GIF (or PNG/JPEG) -> W x H frames. Aspect-fit on black.
    public static func importImage(_ url: URL, w: Int = 32, h: Int = 32) throws -> Display {
        _ = try readLimited(url)
        guard let src = CGImageSourceCreateWithURL(url as CFURL, nil) else { throw uerr(2, "cannot read image") }
        let n = CGImageSourceGetCount(src)
        if n > Limits.maxFramesRead { throw uerr(5, "too many frames (\(n) > \(Limits.maxFramesRead))") }
        var frames = [Frame]()
        for i in 0..<n {
            let props = CGImageSourceCopyPropertiesAtIndex(src, i, nil) as? [CFString: Any]
            let pw = props?[kCGImagePropertyPixelWidth] as? Int ?? 0, ph = props?[kCGImagePropertyPixelHeight] as? Int ?? 0
            if pw * ph > Limits.maxImagePixels { throw uerr(7, "image too large (\(pw)x\(ph))") }
            guard let img = CGImageSourceCreateImageAtIndex(src, i, nil) else { continue }
            var delay = 0.1
            if let gif = props?[kCGImagePropertyGIFDictionary] as? [CFString: Any] {
                if let u = gif[kCGImagePropertyGIFUnclampedDelayTime] as? Double, u > 0.001 { delay = u }
                else if let dd = gif[kCGImagePropertyGIFDelayTime] as? Double, dd > 0.001 { delay = dd }
            }
            frames.append(Frame(px: rasterize(img, w, h), dur: max(20, min(60000, Int(delay * 1000 + 0.5)))))
        }
        if frames.count > MAX_FRAMES {      // keep total timing, drop frames evenly
            let step = Int((Double(frames.count) / Double(MAX_FRAMES)).rounded(.up))
            var out = [Frame](); var i = 0
            while i < frames.count {
                var f = frames[i]
                f.dur = min(60000, frames[i..<min(i + step, frames.count)].reduce(0) { $0 + $1.dur })
                out.append(f); i += step
            }
            frames = out
        }
        if frames.isEmpty { throw uerr(3, "no frames") }
        return Display(name: url.deletingPathExtension().lastPathComponent, desc: "imported from " + url.lastPathComponent, w: w, h: h, frames: frames)
    }
    static func rasterize(_ img: CGImage, _ W: Int, _ H: Int) -> [UInt32] {
        var buf = [UInt8](repeating: 0, count: W * H * 4)
        buf.withUnsafeMutableBytes { p in
            guard let ctx = CGContext(data: p.baseAddress, width: W, height: H, bitsPerComponent: 8, bytesPerRow: W * 4,
                                      space: CGColorSpaceCreateDeviceRGB(),
                                      bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return }
            ctx.setFillColor(CGColor(red: 0, green: 0, blue: 0, alpha: 1))
            ctx.fill(CGRect(x: 0, y: 0, width: W, height: H))
            ctx.interpolationQuality = (img.width <= W && img.height <= H) ? .none : .high
            let s = min(Double(W) / Double(img.width), Double(H) / Double(img.height))
            let dw = Double(img.width) * s, dh = Double(img.height) * s
            ctx.draw(img, in: CGRect(x: (Double(W) - dw) / 2, y: (Double(H) - dh) / 2, width: dw, height: dh))
        }
        var px = [UInt32](repeating: 0, count: W * H)
        for i in 0..<(W * H) {
            var r = UInt32(buf[i * 4]), g = UInt32(buf[i * 4 + 1]), b = UInt32(buf[i * 4 + 2])
            if r + g + b < 12 { r = 0; g = 0; b = 0 }       // near-black -> off (LEDs)
            px[i] = r << 16 | g << 8 | b
        }
        return px
    }
}

extension String { func leftPad(_ n: Int) -> String { count >= n ? self : String(repeating: "0", count: n - count) + self } }

// ---------- playlists (stored locally in <library>/Playlists/<name>.json, sent to the board on Activate) ----------
public struct PlaylistItem: Identifiable, Equatable, Codable {
    public var id = UUID()
    public var mode: String          // "clock", "agenda", ... or "custom:<name>"
    public var seconds: Int
    public var file: String?         // library-relative JSON for custom items (auto-upload source)
    enum CodingKeys: String, CodingKey { case mode, seconds, file }
    public init(mode: String, seconds: Int = 10, file: String? = nil) { self.mode = mode; self.seconds = seconds; self.file = file }
}
public struct Playlist: Codable, Equatable { public var name: String; public var items: [PlaylistItem] }

public enum PlaylistIO {
    public static var dir: URL { DisplayIO.libDir.appendingPathComponent("Playlists") }
    public static func list() -> [String] {
        let f = (try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []
        return f.filter { $0.hasSuffix(".json") }.map { String($0.dropLast(5)) }.sorted()
    }
    public static func load(_ name: String) -> Playlist? {
        guard let d = try? Data(contentsOf: dir.appendingPathComponent(name + ".json")), d.count < 1_000_000 else { return nil }
        return try? JSONDecoder().decode(Playlist.self, from: d)
    }
    public static func save(_ p: Playlist) throws {
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let e = JSONEncoder(); e.outputFormatting = [.prettyPrinted, .sortedKeys]
        try e.encode(p).write(to: dir.appendingPathComponent(p.name + ".json"), options: .atomic)
    }
    public static func delete(_ name: String) { try? FileManager.default.removeItem(at: dir.appendingPathComponent(name + ".json")) }
    /// What the board receives (no local file paths).
    public static func boardJSON(_ p: Playlist) -> Data {
        let items: [[String: Any]] = p.items.map { ["mode": $0.mode, "seconds": max(2, min(3600, $0.seconds))] }
        return (try? JSONSerialization.data(withJSONObject: ["name": p.name, "items": items])) ?? Data()
    }
}

// ---------- connection settings: UserDefaults + Keychain (password), optional config file ----------
public struct ConnConfig: Equatable {
    public var host = "", port: UInt16 = 1883, user = "", pass = "", base = "", tls = false
    public var complete: Bool { !host.isEmpty && !user.isEmpty && !pass.isEmpty }
}

public enum Config {
    static let svc = "Unicorn Studio MQTT"
    static let ud = UserDefaults.standard
    /// Optional KEY=VALUE file: $UNICORN_CONFIG or ~/Library/Application Support/Unicorn Studio/config.env.
    /// It may point at another file with SECRETS_FILE=/path (read the same way; first value wins).
    public static func fileValues() -> [String: String] {
        let env = ProcessInfo.processInfo.environment
        let def = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Unicorn Studio/config.env").path
        var out = [String: String]()
        var queue = [env["UNICORN_CONFIG"] ?? env["UNICORN_SECRETS"] ?? def]
        var seen = Set<String>()
        while let p0 = queue.first {
            queue.removeFirst()
            let p = (p0 as NSString).expandingTildeInPath
            if seen.contains(p) || seen.count > 3 { continue }; seen.insert(p)
            guard let s = try? String(contentsOfFile: p, encoding: .utf8) else { continue }
            for line in s.split(separator: "\n") {
                let t = line.trimmingCharacters(in: .whitespaces)
                if t.hasPrefix("#") || !t.contains("=") { continue }
                let kv = t.split(separator: "=", maxSplits: 1).map(String.init)
                var v = kv.count > 1 ? kv[1].trimmingCharacters(in: .whitespaces) : ""
                if v.count >= 2, (v.hasPrefix("\"") && v.hasSuffix("\"")) || (v.hasPrefix("'") && v.hasSuffix("'")) { v = String(v.dropFirst().dropLast()) }
                let k = kv[0].replacingOccurrences(of: "export ", with: "").trimmingCharacters(in: .whitespaces)
                if k == "SECRETS_FILE" { queue.append(v); continue }
                if out[k] == nil { out[k] = v }
            }
        }
        return out
    }
    public static func load() -> ConnConfig {
        var c = ConnConfig()
        let f = fileValues()
        c.host = ud.string(forKey: "host") ?? f["MQTT_BROKER"] ?? ""
        c.port = UInt16(ud.string(forKey: "port") ?? f["MQTT_PORT"] ?? "1883") ?? 1883
        c.user = ud.string(forKey: "user") ?? f["MQTT_USER"] ?? ""
        c.base = ud.string(forKey: "base") ?? f["UNICORN_BASE"] ?? ""
        c.tls = ud.object(forKey: "tls") != nil ? ud.bool(forKey: "tls") : (f["MQTT_TLS"] == "1")
        c.pass = keychainGet(c.user) ?? f["MQTT_PASSWORD"] ?? ""
        return c
    }
    public static func save(_ c: ConnConfig) {
        ud.set(c.host, forKey: "host"); ud.set(String(c.port), forKey: "port"); ud.set(c.user, forKey: "user")
        ud.set(c.base, forKey: "base"); ud.set(c.tls, forKey: "tls")
        if !c.pass.isEmpty { keychainSet(c.user, c.pass) }
    }
    static func keychainGet(_ acct: String) -> String? {
        guard !acct.isEmpty else { return nil }
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: svc,
                                kSecAttrAccount as String: acct, kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let d = out as? Data else { return nil }
        return String(data: d, encoding: .utf8)
    }
    static func keychainSet(_ acct: String, _ pw: String) {
        let base: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: svc, kSecAttrAccount as String: acct]
        SecItemDelete(base as CFDictionary)
        var add = base; add[kSecValueData as String] = Data(pw.utf8)
        add[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlocked
        SecItemAdd(add as CFDictionary, nil)
    }
}

// ---------- 3x5 pixel font (same glyphs as the board) ----------
public enum PixelFont {
    static let g: [Character: String] = [
        "A": ".#.#.#####.##.#", "B": "##.#.###.#.###.", "C": ".###..#..#...##", "D": "##.#.##.##.###.",
        "E": "####..##.#..###", "F": "####..##.#..#..", "G": ".###..#.##.#.##", "H": "#.##.#####.##.#",
        "I": "###.#..#..#.###", "J": "..#..#..##.#.#.", "K": "#.##.###.#.##.#", "L": "#..#..#..#..###",
        "M": "#.########.##.#", "N": "##.#.##.##.##.#", "O": ".#.#.##.##.#.#.", "P": "##.#.###.#..#..",
        "Q": ".#.#.##.###..##", "R": "##.#.###.#.##.#", "S": ".###...#...###.", "T": "###.#..#..#..#.",
        "U": "#.##.##.##.####", "V": "#.##.##.##.#.#.", "W": "#.##.########.#", "X": "#.##.#.#.#.##.#",
        "Y": "#.##.#.#..#..#.", "Z": "###..#.#.#..###", "0": "####.##.##.####", "1": ".#.##..#..#.###",
        "2": "##...#.#.#..###", "3": "##...#.#...###.", "4": "#.##.####..#..#", "5": "####..##...###.",
        "6": ".###..####.####", "7": "###..#.#..#..#.", "8": "####.#####.####", "9": "####.####..###.",
        "/": "..#..#.#.#..#..", "-": "......###......", "&": ".#.#.#.#.#.#.##", "?": "##...#.#.....#.",
        "+": "....#.###.#....", "(": ".#.#..#..#...#.", ")": ".#...#..#..#.#.", "#": "#.####.#.####.#",
        "<": "..#.#.#...#...#", ">": "#...#...#.#.#..", "=": "...###...###...", "*": "#.#.#.#.#.#....",
    ]
    static let narrow: [Character: String] = [":": ".#.#.", ".": "....#", "'": "##...", "!": "###.#", ",": "...##"]
    static func fixed(_ s: String) -> String { s.count == 15 ? s : String((s + "...............").prefix(15)) }
    public static func layout(_ text: String) -> [(Int, Int)] {
        var pts = [(Int, Int)](); var x = 0
        for ch in text.uppercased() {
            if ch == " " { x += 3; continue }
            if let n = narrow[ch] { for (r, c) in n.enumerated() where c == "#" { pts.append((x, r)) }; x += 2; continue }
            let b = Array(fixed(g[ch] ?? g["?"]!))
            for r in 0..<5 { for c in 0..<3 where b[r * 3 + c] == "#" { pts.append((x + c, r)) } }
            x += 4
        }
        return pts
    }
}

// ---------- minimal MQTT 3.1.1 (QoS 0) over Network.framework ----------
public final class MQTT {
    public var onMessage: ((String, Data) -> Void)?
    public var onConnected: ((Bool, String) -> Void)?
    public private(set) var connected = false
    let q = DispatchQueue(label: "unicorn.mqtt")
    var conn: NWConnection?
    var buf = Data()
    var subs: [String] = []
    var cfg = ConnConfig()
    var ping: DispatchSourceTimer?
    var stopped = false
    static let maxPacket = 1 << 20
    public init() {}

    static func vlen(_ n: Int) -> Data {
        var d = Data(); var x = n
        repeat { var b = UInt8(x % 128); x /= 128; if x > 0 { b |= 0x80 }; d.append(b) } while x > 0
        return d
    }
    static func str(_ s: String) -> Data { let u = Data(s.utf8.prefix(65535)); return Data([UInt8(u.count >> 8), UInt8(u.count & 255)]) + u }

    public func start(_ c: ConnConfig, subs: [String]) {
        q.async {
            self.cfg = c; self.subs = subs; self.stopped = false
            self.conn?.cancel(); self.conn = nil
            self.open()
        }
    }
    public func stop() { q.async { self.stopped = true; self.conn?.cancel(); self.conn = nil; self.connected = false; self.ping?.cancel() } }
    func open() {
        guard !stopped, let port = NWEndpoint.Port(rawValue: cfg.port), !cfg.host.isEmpty else { return }
        buf = Data(); connected = false
        let c = NWConnection(host: NWEndpoint.Host(cfg.host), port: port, using: cfg.tls ? .tls : .tcp)
        conn = c
        c.stateUpdateHandler = { [weak self, weak c] st in
            guard let self = self, let c = c, c === self.conn else { return }
            switch st {
            case .ready:
                var vh = MQTT.str("MQTT") + Data([4, 0xC2, 0, 60])
                vh += MQTT.str("unicorn-studio-" + String(Int.random(in: 100000...999999)))
                vh += MQTT.str(self.cfg.user) + MQTT.str(self.cfg.pass)
                self.send(Data([0x10]) + MQTT.vlen(vh.count) + vh)
                self.recv()
            case .failed(let e): self.fail("\(e)")
            case .waiting(let e): self.fail("\(e)")
            default: break
            }
        }
        c.start(queue: q)
    }
    func fail(_ why: String) {
        onConnected?(false, why)
        connected = false
        conn?.cancel(); conn = nil
        ping?.cancel(); ping = nil
        q.asyncAfter(deadline: .now() + 3) { if self.conn == nil { self.open() } }
    }
    func send(_ d: Data) { conn?.send(content: d, completion: .contentProcessed { _ in }) }
    func recv() {
        conn?.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, done, err in
            guard let self = self else { return }
            if let d = data { self.buf += d; if !self.parse() { self.fail("protocol error"); return } }
            if done || err != nil { self.fail(err.map { "\($0)" } ?? "closed"); return }
            self.recv()
        }
    }
    /// false = malformed stream (drop the connection)
    func parse() -> Bool {
        while buf.count >= 2 {
            let b = [UInt8](buf.prefix(5))
            var n = 0, mul = 1, i = 1
            while true {
                if i >= b.count { return buf.count < 5 }
                n += Int(b[i] & 0x7F) * mul; mul *= 128
                if b[i] & 0x80 == 0 { break }
                i += 1
                if i > 4 { return false }
            }
            if n > MQTT.maxPacket { return false }
            let hdr = i + 1
            if buf.count < hdr + n { return true }
            let body = Data(buf[buf.startIndex + hdr ..< buf.startIndex + hdr + n])
            let op = b[0]
            buf = Data(buf.dropFirst(hdr + n))
            if op == 0x20 {
                let rc = body.count >= 2 ? body[body.startIndex + 1] : 255
                if rc == 0 {
                    connected = true
                    var pid: UInt16 = 1
                    for t in subs {
                        let p = Data([UInt8(pid >> 8), UInt8(pid & 255)]) + MQTT.str(t) + Data([0])
                        send(Data([0x82]) + MQTT.vlen(p.count) + p); pid += 1
                    }
                    let tm = DispatchSource.makeTimerSource(queue: q)
                    tm.schedule(deadline: .now() + 30, repeating: 30)
                    tm.setEventHandler { [weak self] in self?.send(Data([0xC0, 0])) }
                    tm.resume(); ping = tm
                    onConnected?(true, "connected")
                } else {
                    onConnected?(false, rc == 4 || rc == 5 ? "broker refused the login (check user/password)" : "broker refused (code \(rc))")
                    stopped = true; conn?.cancel(); conn = nil
                }
            } else if op & 0xF0 == 0x30 {
                let bb = [UInt8](body)
                guard bb.count >= 2 else { continue }
                let tl = Int(bb[0]) << 8 | Int(bb[1])
                guard bb.count >= 2 + tl else { continue }
                let topic = String(decoding: bb[2..<2 + tl], as: UTF8.self)
                let p = 2 + tl + ((op & 6) != 0 ? 2 : 0)
                onMessage?(topic, Data(bb[min(p, bb.count)...]))
            }
        }
        return true
    }
    public func publish(_ topic: String, _ payload: Data, retain: Bool = false) {
        q.async {
            let v = MQTT.str(topic) + payload
            self.send(Data([0x30 | (retain ? 1 : 0)]) + MQTT.vlen(v.count) + v)
        }
    }
    public func publish(_ topic: String, _ s: String, retain: Bool = false) { publish(topic, Data(s.utf8), retain: retain) }
}

// ---------- uploader: one upload at a time, per-frame ack with retry, success only on the board's final OK ----------
public struct UploadResult { public let ok: Bool; public let msg: String }

public final class Uploader {
    let mq: MQTT; public var base: String
    /// v2 = firmware "us-*" (session id, retries, JSON result). v1 = older firmware (plain acks/results).
    public var v2 = true
    let sem = DispatchSemaphore(value: 0)
    let gate = NSLock()                   // serialises uploads
    var lock = NSLock()
    var active = false; var uid = ""; var wantAck = ""; var acked = false; var result: UploadResult?
    public init(mq: MQTT, base: String) { self.mq = mq; self.base = base }

    /// Feed every message on BASE/custom/ack, /custom/result, /custom/result2.
    public func feed(_ topic: String, _ p: Data) {
        let s = String(decoding: p, as: UTF8.self)
        lock.lock(); defer { lock.unlock() }
        if !active { return }
        if topic.hasSuffix("/custom/ack") {
            if s == wantAck { acked = true; sem.signal() }
        } else if v2 && topic.hasSuffix("/custom/result2") {
            guard let j = try? JSONSerialization.jsonObject(with: p) as? [String: Any], (j["id"] as? String) == uid else { return }
            result = UploadResult(ok: j["ok"] as? Bool ?? false, msg: j["msg"] as? String ?? "?"); sem.signal()
        } else if !v2 && topic.hasSuffix("/custom/result") {
            result = UploadResult(ok: s.hasPrefix("ok "), msg: s); sem.signal()
        }
    }
    func waitEvent(_ secs: Double) -> Bool { sem.wait(timeout: .now() + secs) == .success }

    /// Blocking; run off the main thread. `frames` must already be at the board's size.
    public func send(name: String, frames: [Frame], play: Bool = true, progress: (Int, Int) -> Void) -> UploadResult {
        guard gate.try() else { return UploadResult(ok: false, msg: "another upload is already running") }
        defer { lock.lock(); active = false; uid = ""; wantAck = ""; lock.unlock(); gate.unlock() }
        let fr = Array(frames.prefix(MAX_FRAMES))
        guard !fr.isEmpty else { return UploadResult(ok: false, msg: "nothing to send") }
        let id = String(UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(8)).lowercased()
        while sem.wait(timeout: .now()) == .success {}
        lock.lock(); active = true; uid = v2 ? id : ""; wantAck = ""; acked = false; result = nil; lock.unlock()
        var begin: [String: Any] = ["name": name, "durations": fr.map { $0.dur }]
        if v2 { begin["id"] = id }
        mq.publish(base + "/custom/begin", try! JSONSerialization.data(withJSONObject: begin))
        let chunk = 1024
        for (i, f) in fr.enumerated() {
            let rgb = f.rgb()
            let nch = (rgb.count + chunk - 1) / chunk
            lock.lock(); wantAck = v2 ? "\(id):\(i + 1)" : String(i + 1); acked = false; lock.unlock()
            var ok = false
            for attempt in 0..<(v2 ? 4 : 1) {
                for c in 0..<nch {
                    var m = v2 ? Data([0xA5, UInt8(id.utf8.count)]) + Data(id.utf8) : Data()
                    m += Data([UInt8(i >> 8), UInt8(i & 255), UInt8(c)])
                    m += rgb[c * chunk ..< min(rgb.count, (c + 1) * chunk)]
                    mq.publish(base + "/custom/frame", m)
                }
                let deadline = Date().addingTimeInterval(attempt == 0 ? 6 : 4)
                while Date() < deadline {
                    _ = waitEvent(deadline.timeIntervalSinceNow)
                    lock.lock(); let a = acked, r = result; lock.unlock()
                    if let r = r { return UploadResult(ok: false, msg: "board: " + r.msg) }   // any result mid-upload = error
                    if a { ok = true; break }
                }
                if ok { break }
            }
            if !ok { return UploadResult(ok: false, msg: "board did not confirm frame \(i + 1) of \(fr.count); nothing was saved (is the board online?)") }
            progress(i + 1, fr.count)
        }
        lock.lock(); wantAck = ""; lock.unlock()
        if v2 {
            mq.publish(base + "/custom/end", try! JSONSerialization.data(withJSONObject: ["name": name, "id": id, "play": play]))
        } else { mq.publish(base + "/custom/end", name) }
        let deadline = Date().addingTimeInterval(20)
        while Date() < deadline {
            _ = waitEvent(deadline.timeIntervalSinceNow)
            lock.lock(); let r = result; lock.unlock()
            if let r = r { return UploadResult(ok: r.ok, msg: "board: " + r.msg) }
        }
        return UploadResult(ok: false, msg: "no final answer from the board within 20 s; it may not have saved \(name)")
    }
    public func live(_ f: Frame) {
        let rgb = f.rgb(); let n = (rgb.count + 1023) / 1024
        for c in 0..<n { mq.publish(base + "/custom/live", Data([UInt8(c)]) + rgb[c * 1024 ..< min(rgb.count, (c + 1) * 1024)]) }
    }
    public func liveOff() { mq.publish(base + "/custom/live", Data()) }
}
