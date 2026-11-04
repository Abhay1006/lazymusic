// lazymusic-helper: runs lazymusic's AppleScript in-process, so a call to
// Music.app does not have to start `osascript` and compile the script again
// each time.
//
// lazymusic starts one of these per worker thread and talks to it over
// stdin/stdout, one JSON object per line:
//
//   helper  -> {"hello": "lazymusic-helper", "protocol": 1}     once, at start
//   python  -> {"script": "tell application \"Music\" ..."}
//   helper  -> {"ok": true, "out": "playing\u001f80..."}
//          or {"ok": false, "error": "Music got an error: ...", "code": -1728}
//
// Requests are answered one at a time and in order. The helper exits when
// stdin closes, so it goes away with lazymusic however lazymusic ends.
//
// Build with `helper/build.sh`. It needs only the Swift compiler that comes
// with Xcode's command line tools.

import Foundation

let protocolVersion = 1

// Every `osascript` run starts with the text item delimiters at "", and the
// scripts are written for that. In here one AppleScript instance runs every
// script, so a delimiter set by one script would still be set for the next.
// Resetting it first makes each run behave like a fresh `osascript`.
let preamble = "set AppleScript's text item delimiters to \"\"\n"

func fourCC(_ s: String) -> FourCharCode {
    return s.utf8.reduce(0) { ($0 << 8) | FourCharCode($1) }
}

let descList = fourCC("list")
let descNull = fourCC("null")
let descBool = fourCC("bool")
let descTrue = fourCC("true")
let descFalse = fourCC("fals")
let descLong = fourCC("long")
let descDouble = fourCC("doub")
let descTypeCode = fourCC("type")
let descMissing = fourCC("msng")

/// The result as text, formatted the way `osascript` prints it.
///
/// lazymusic's scripts already return text, so this is mostly the string
/// itself. The other cases cover commands whose result nobody reads, such as
/// `set sound volume to 80`.
func text(of d: NSAppleEventDescriptor) -> String {
    switch d.descriptorType {
    case descNull:
        return ""
    case descTrue:
        return "true"
    case descFalse:
        return "false"
    case descBool:
        return d.booleanValue ? "true" : "false"
    case descLong:
        return String(d.int32Value)
    case descDouble:
        return String(d.doubleValue)
    case descTypeCode where d.typeCodeValue == descMissing:
        return "missing value"
    case descList:
        let n = d.numberOfItems
        if n == 0 {
            return ""
        }
        return (1...n).map { i in d.atIndex(i).map(text(of:)) ?? "" }
            .joined(separator: ", ")
    default:
        return d.stringValue ?? ""
    }
}

func failure(_ info: NSDictionary?, fallback: String) -> [String: Any] {
    let message = info?["NSAppleScriptErrorMessage"] as? String
        ?? info?["NSAppleScriptErrorBriefMessage"] as? String
        ?? fallback
    let code = (info?["NSAppleScriptErrorNumber"] as? NSNumber)?.intValue ?? 0
    // Error numbers are part of the message, as `osascript` writes them:
    // lazymusic checks for some, such as -10006 when a track is not yet
    // seekable.
    let shown = code != 0 ? "\(message) (\(code))" : message
    return ["ok": false, "error": shown, "code": code]
}

// Each distinct script is compiled once and kept. The status poll sends the
// same text every time, so after the first call it costs only the Apple
// Events themselves. Scripts with an ID or a search spliced into them are
// one-offs, so the cache is capped and the oldest entry goes first.
final class Runner {
    let limit = 64
    var cache: [String: NSAppleScript] = [:]
    var order: [String] = []

    func compiled(_ source: String) -> (NSAppleScript?, [String: Any]?) {
        if let script = cache[source] {
            return (script, nil)
        }
        guard let script = NSAppleScript(source: preamble + source) else {
            return (nil, ["ok": false, "error": "could not load the script", "code": 0])
        }
        var info: NSDictionary?
        if !script.compileAndReturnError(&info) {
            return (nil, failure(info, fallback: "the script did not compile"))
        }
        cache[source] = script
        order.append(source)
        if order.count > limit {
            cache.removeValue(forKey: order.removeFirst())
        }
        return (script, nil)
    }

    func run(_ source: String) -> [String: Any] {
        let (found, problem) = compiled(source)
        guard let script = found else {
            return problem!
        }
        var info: NSDictionary?
        // Optional either way: some SDKs declare this result non-optional,
        // but it is nil whenever the script fails.
        let result: NSAppleEventDescriptor? = script.executeAndReturnError(&info)
        if let info = info {
            return failure(info, fallback: "the script failed")
        }
        return ["ok": true, "out": result.map(text(of:)) ?? ""]
    }
}

func send(_ object: [String: Any]) {
    guard var data = try? JSONSerialization.data(withJSONObject: object) else {
        // Only text from a script can get here, and only if it is not valid
        // Unicode. The caller still gets an answer, so it is not left waiting.
        send(["ok": false, "error": "the result could not be encoded", "code": 0])
        return
    }
    data.append(0x0A)
    FileHandle.standardOutput.write(data)
}

// Everything lives inside this function rather than at the top level, so
// there is no shared mutable state for Swift's concurrency checks to object to.
func serve() {
    let runner = Runner()
    send(["hello": "lazymusic-helper", "protocol": protocolVersion])
    while let line = readLine(strippingNewline: true) {
        autoreleasepool {
            if line.isEmpty {
                return
            }
            guard let data = line.data(using: .utf8),
                  let request = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                  let source = request["script"] as? String
            else {
                send(["ok": false, "error": "lazymusic-helper got a malformed request", "code": 0])
                return
            }
            send(runner.run(source))
        }
    }
}

serve()
