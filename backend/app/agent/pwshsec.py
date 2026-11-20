"""按命令名判断 PowerShell，而不是在整段字符串里找危险词。

先去掉引号和注释，把语句、管道拆开，别名还原成 cmdlet，再决定拒绝还是只读。
Format-Table、Get-Date -Format 这类显示命令因此不会被当成格式化磁盘。
"""

import re

_ALIASES = {
    "ls": "get-childitem",
    "dir": "get-childitem",
    "gci": "get-childitem",
    "cat": "get-content",
    "type": "get-content",
    "gc": "get-content",
    "cd": "set-location",
    "sl": "set-location",
    "chdir": "set-location",
    "pushd": "push-location",
    "popd": "pop-location",
    "pwd": "get-location",
    "gl": "get-location",
    "gi": "get-item",
    "gp": "get-itemproperty",
    "ni": "new-item",
    "mkdir": "new-item",
    "md": "new-item",
    "ri": "remove-item",
    "del": "remove-item",
    "rd": "remove-item",
    "rmdir": "remove-item",
    "rm": "remove-item",
    "erase": "remove-item",
    "mi": "move-item",
    "mv": "move-item",
    "move": "move-item",
    "ci": "copy-item",
    "cp": "copy-item",
    "copy": "copy-item",
    "cpi": "copy-item",
    "si": "set-item",
    "rni": "rename-item",
    "ren": "rename-item",
    "ps": "get-process",
    "gps": "get-process",
    "kill": "stop-process",
    "spps": "stop-process",
    "start": "start-process",
    "saps": "start-process",
    "sajb": "start-job",
    "ipmo": "import-module",
    "echo": "write-output",
    "write": "write-output",
    "sleep": "start-sleep",
    "help": "get-help",
    "man": "get-help",
    "gcm": "get-command",
    "gsv": "get-service",
    "gv": "get-variable",
    "sv": "set-variable",
    "h": "get-history",
    "history": "get-history",
    "iex": "invoke-expression",
    "iwr": "invoke-webrequest",
    "irm": "invoke-restmethod",
    "icm": "invoke-command",
    "ii": "invoke-item",
    "nsn": "new-pssession",
    "etsn": "enter-pssession",
    "exsn": "exit-pssession",
    "gsn": "get-pssession",
    "rsn": "remove-pssession",
    "cls": "clear-host",
    "clear": "clear-host",
    "select": "select-object",
    "where": "where-object",
    "foreach": "foreach-object",
    "%": "foreach-object",
    "?": "where-object",
    "measure": "measure-object",
    "ft": "format-table",
    "fl": "format-list",
    "fw": "format-wide",
    "oh": "out-host",
    "ogv": "out-gridview",
    "ac": "add-content",
    "clc": "clear-content",
    "tee": "tee-object",
    "epcsv": "export-csv",
    "sp": "set-itemproperty",
    "rp": "remove-itemproperty",
    "cli": "clear-item",
    "epal": "export-alias",
    "sls": "select-string",
}


def _flags(*names: str) -> dict:
    return {"flags": {name.lower() for name in names}}


def _all_flags() -> dict:
    return {"all_flags": True}


# 只读白名单。allow_all 的命令仍会拒绝脚本块、变量和计算属性。
_ALLOW = {
    "get-childitem": _flags(
        "-path", "-literalpath", "-filter", "-include", "-exclude", "-recurse", "-depth",
        "-name", "-force", "-attributes", "-directory", "-file", "-hidden", "-readonly", "-system",
    ),
    "get-content": _flags(
        "-path", "-literalpath", "-totalcount", "-head", "-tail", "-raw", "-encoding", "-delimiter", "-readcount",
    ),
    "get-item": _flags("-path", "-literalpath", "-force", "-stream"),
    "get-itemproperty": _flags("-path", "-literalpath", "-name"),
    "test-path": _flags(
        "-path", "-literalpath", "-pathtype", "-filter", "-include", "-exclude", "-isvalid", "-newerthan", "-olderthan",
    ),
    "resolve-path": _flags("-path", "-literalpath", "-relative"),
    "get-filehash": _flags("-path", "-literalpath", "-algorithm", "-inputstream"),
    "get-acl": _flags("-path", "-literalpath", "-audit", "-filter", "-include", "-exclude"),
    "set-location": _flags("-path", "-literalpath", "-passthru", "-stackname"),
    "push-location": _flags("-path", "-literalpath", "-passthru", "-stackname"),
    "pop-location": _flags("-passthru", "-stackname"),
    "select-string": _flags(
        "-path", "-literalpath", "-pattern", "-inputobject", "-simplematch", "-casesensitive", "-quiet",
        "-list", "-notmatch", "-allmatches", "-encoding", "-context", "-raw", "-noemphasis",
    ),
    "convertto-json": _flags("-inputobject", "-depth", "-compress", "-enumsasstrings", "-asarray"),
    "convertfrom-json": _flags("-inputobject", "-depth", "-ashashtable", "-noenumerate"),
    "convertto-csv": _flags("-inputobject", "-delimiter", "-notypeinformation", "-noheader", "-usequotes"),
    "convertfrom-csv": _flags("-inputobject", "-delimiter", "-header", "-useculture"),
    "convertto-xml": _flags("-inputobject", "-depth", "-as", "-notypeinformation"),
    "convertto-html": _flags("-inputobject", "-property", "-head", "-title", "-body", "-pre", "-post", "-as", "-fragment"),
    "format-hex": _flags("-path", "-literalpath", "-inputobject", "-encoding", "-count", "-offset"),
    "get-member": _flags("-inputobject", "-membertype", "-name", "-static", "-view", "-force"),
    "get-unique": _flags("-inputobject", "-asstring", "-caseinsensitive", "-ontype"),
    "compare-object": _flags(
        "-referenceobject", "-differenceobject", "-property", "-syncwindow", "-casesensitive", "-culture",
        "-excludedifferent", "-includeequal", "-passthru",
    ),
    "join-string": _flags("-inputobject", "-property", "-separator", "-outputprefix", "-outputsuffix", "-singlequote", "-doublequote", "-formatstring"),
    "get-random": _flags("-inputobject", "-minimum", "-maximum", "-count", "-setseed", "-shuffle"),
    "convert-path": _flags("-path", "-literalpath"),
    "join-path": _flags("-path", "-childpath", "-additionalchildpath"),
    "split-path": _flags(
        "-path", "-literalpath", "-qualifier", "-noqualifier", "-parent", "-leaf", "-leafbase", "-extension", "-isabsolute",
    ),
    "get-hotfix": _flags("-id", "-description"),
    "get-itempropertyvalue": _flags("-path", "-literalpath", "-name"),
    "get-psprovider": _flags("-psprovider"),
    "get-process": _flags("-name", "-id", "-module", "-fileversioninfo", "-includeusername"),
    "get-service": _flags("-name", "-displayname", "-dependentservices", "-requiredservices", "-include", "-exclude"),
    "get-computerinfo": _all_flags(),
    "get-host": _all_flags(),
    "get-date": _flags("-date", "-format", "-uformat", "-displayhint", "-asutc"),
    "get-location": _flags("-psprovider", "-psdrive", "-stack", "-stackname"),
    "get-psdrive": _flags("-name", "-psprovider", "-scope"),
    "get-module": _flags("-name", "-listavailable", "-all", "-fullyqualifiedname", "-psedition"),
    "get-alias": _flags("-name", "-definition", "-scope", "-exclude"),
    "get-history": _flags("-id", "-count"),
    "get-culture": _all_flags(),
    "get-uiculture": _all_flags(),
    "get-timezone": _flags("-name", "-id", "-listavailable"),
    "get-uptime": _all_flags(),
    "write-output": _flags("-inputobject", "-noenumerate"),
    "write-host": _flags("-object", "-nonewline", "-separator", "-foregroundcolor", "-backgroundcolor"),
    "start-sleep": _flags("-seconds", "-milliseconds", "-duration"),
    "format-table": _all_flags(),
    "format-list": _all_flags(),
    "format-wide": _all_flags(),
    "format-custom": _all_flags(),
    "measure-object": _all_flags(),
    "select-object": _all_flags(),
    "sort-object": _all_flags(),
    "group-object": _all_flags(),
    "where-object": _all_flags(),
    "out-string": _all_flags(),
    "out-host": _all_flags(),
    "get-netadapter": _flags("-name", "-interfacedescription", "-interfaceindex", "-physical"),
    "get-netipaddress": _flags("-interfaceindex", "-interfacealias", "-addressfamily", "-type"),
    "get-netipconfiguration": _flags("-interfaceindex", "-interfacealias", "-detailed", "-all"),
    "get-netroute": _flags("-interfaceindex", "-interfacealias", "-addressfamily", "-destinationprefix"),
    "get-dnsclientcache": _flags("-entry", "-name", "-type", "-status", "-section", "-data"),
    "get-dnsclient": _flags("-interfaceindex", "-interfacealias"),
    "get-eventlog": _flags(
        "-logname", "-newest", "-after", "-before", "-entrytype", "-index", "-instanceid", "-message",
        "-source", "-username", "-asbaseobject", "-list",
    ),
    "get-winevent": _flags("-logname", "-listlog", "-listprovider", "-providername", "-path", "-maxevents", "-filterxpath", "-force", "-oldest"),
    "get-cimclass": _flags("-classname", "-namespace", "-methodname", "-propertyname", "-qualifiername"),
    "ipconfig": {**_flags("/all", "/displaydns", "/allcompartments"), "no_positional": True},
    "netstat": _flags("-a", "-b", "-e", "-f", "-n", "-o", "-p", "-q", "-r", "-s", "-t", "-x", "-y"),
    "systeminfo": _flags("/fo", "/nh"),
    "tasklist": _flags("/m", "/svc", "/v", "/fi", "/fo", "/nh"),
    "where.exe": _all_flags(),
    "hostname": {**_flags("-a", "-d", "-f", "-i", "-s", "-y", "-a"), "no_positional": True},
    "whoami": _flags("/user", "/groups", "/claims", "/priv", "/logonid", "/all", "/fo", "/nh"),
    "ver": _all_flags(),
    "arp": _flags("-a", "-g", "-v", "-n"),
    "route": {**_flags("-4", "-6"), "verb": "print"},
}

_GIT_READ = {"status", "log", "diff", "show", "rev-parse", "remote", "describe", "branch"}
_SHELLS = {"cmd", "powershell", "pwsh", "bash", "sh", "wsl"}
_EXEC = {
    "invoke-expression", "invoke-webrequest", "invoke-restmethod", "invoke-command", "invoke-item",
    "start-process", "start-job", "import-module", "new-item", "set-content", "add-content", "out-file",
    "remove-item", "move-item", "copy-item", "rename-item", "clear-content", "set-alias", "new-alias",
    "set-variable", "new-object", "add-type", "foreach-object", "stop-process", "new-pssession",
    "enter-pssession", "register-scheduledjob", "start-bitstransfer",
}
_KNOWN = set(_ALLOW) | set(_ALIASES.values()) | _EXEC | {
    "format", "format-volume", "shutdown", "restart-computer", "stop-computer", "diskpart", "clear-disk", "cipher",
}
_SAFE_REDIR = re.compile(r"[0-6*]*>&[0-6*]")
_DRIVE_ROOT = re.compile(r"^[a-z]:\\?$", re.IGNORECASE)
_ENCODED = re.compile(r"^-(e|en|enc|enco|encod|encode|encoded|encodedc|encodedco|encodedcom|encodedcomm|encodedcomma|encodedcomman|encodedcommand)$", re.IGNORECASE)


class _Cmd:
    def __init__(self):
        self.argv: list[str] = []
        self.leaks: list[bool] = []
        self.unsafe_redir = False

    def add(self, text: str, leak: bool) -> None:
        self.argv.append(text)
        self.leaks.append(leak)


def canonical(token: str) -> str:
    name = (token or "").strip().lower()
    if name in _ALIASES:
        return _ALIASES[name]
    return name


def _bare(name: str) -> str:
    for suffix in (".exe", ".com"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _is_flag(token: str) -> bool:
    return bool(re.match(r"^[-/][A-Za-z]", token or ""))


def _leaky(text: str) -> bool:
    return any(piece in text for piece in ("$", "{", "}", "`", "@{", "@(", "::", "--%"))


def _read_group(source: str, i: int):
    opener = source[i]
    brace = 1 if opener == "{" else 0
    paren = 1 if opener == "(" else 0
    start = i
    i += 1
    in_single = in_double = False
    while i < len(source):
        char = source[i]
        if in_single:
            if char == "'" and i + 1 < len(source) and source[i + 1] == "'":
                i += 2
                continue
            if char == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if char == "`" and i + 1 < len(source):
                i += 2
                continue
            if char == '"':
                in_double = False
            i += 1
            continue
        if char == "'":
            in_single = True
        elif char == '"':
            in_double = True
        elif char == "{":
            brace += 1
        elif char == "}":
            brace -= 1
            if brace < 0:
                return None
        elif char == "(":
            paren += 1
        elif char == ")":
            paren -= 1
            if paren < 0:
                return None
        i += 1
        if brace == 0 and paren == 0:
            return source[start:i], True, i
    return None


def parse_commands(source: str) -> list[_Cmd] | None:
    text = source or ""
    length = len(text)
    index = 0
    commands: list[_Cmd] = []
    current = _Cmd()

    def finish() -> None:
        nonlocal current
        if current.argv or current.unsafe_redir:
            commands.append(current)
        current = _Cmd()

    while index < length:
        char = text[index]
        if char in " \t\r":
            index += 1
            continue
        if char == "\n" or char == ";":
            finish()
            index += 1
            continue
        if text.startswith("&&", index) or text.startswith("||", index):
            finish()
            index += 2
            continue
        if char == "|":
            finish()
            index += 1
            continue
        if char == "#":
            while index < length and text[index] != "\n":
                index += 1
            continue
        if char == "`" and index + 1 < length and text[index + 1] == "\n":
            index += 2
            continue
        matched = _SAFE_REDIR.match(text, index)
        if matched and (index == 0 or text[index - 1] in " \t\r\n;|&"):
            index = matched.end()
            continue
        if char in "<>":
            current.unsafe_redir = True
            index += 2 if index + 1 < length and text[index + 1] == ">" else 1
            continue
        if text.startswith("@'", index) or text.startswith('@"', index):
            expandable = text[index + 1] == '"'
            index += 2
            if index < length and text[index] == "\r":
                index += 1
            if index >= length or text[index] != "\n":
                return None
            closer = '\n"@' if expandable else "\n'@"
            end = text.find(closer, index)
            if end < 0:
                return None
            body = text[index:end]
            current.add(body, expandable and _leaky(body))
            index = end + len(closer)
            continue
        if text.startswith("@{", index) or text.startswith("@(", index):
            grouped = _read_group(text, index + 1)
            if grouped is None:
                return None
            body, leak, index = grouped
            current.add("@" + body, True or leak)
            continue
        if char in "{(":
            grouped = _read_group(text, index)
            if grouped is None:
                return None
            body, leak, index = grouped
            current.add(body, leak)
            continue
        if char in "'\"":
            quoted = _read_quote(text, index, char)
            if quoted is None:
                return None
            body, leak, index = quoted
            current.add(body, leak)
            continue
        if char == "$":
            if text.startswith("$(", index):
                grouped = _read_group(text, index + 1)
                if grouped is None:
                    return None
                body, leak, index = grouped
                current.add("$" + body, True)
                continue
            end = index + 1
            if end < length and text[end] == "{":
                grouped = _read_group(text, end)
                if grouped is None:
                    return None
                body, leak, index = grouped
                current.add("$" + body, True)
                continue
            while end < length and (text[end].isalnum() or text[end] in ":_."):
                end += 1
            current.add(text[index:end], True)
            index = end
            continue
        if char in "&,=":
            current.add(char, False)
            index += 1
            continue
        end = index
        leak = False
        while end < length and text[end] not in " \t\r\n;|&#<>'\"$,=":
            if text[end] == "`":
                leak = True
                end += 2 if end + 1 < length else 1
                continue
            end += 1
        word = text[index:end]
        current.add(word, leak or _leaky(word))
        index = end
    finish()
    return commands


def _read_quote(source: str, index: int, mark: str):
    index += 1
    chars: list[str] = []
    leak = False
    while index < len(source):
        char = source[index]
        if mark == "'" and char == "'":
            if index + 1 < len(source) and source[index + 1] == "'":
                chars.append("'")
                index += 2
                continue
            return "".join(chars), False, index + 1
        if mark == '"' and char == "`" and index + 1 < len(source):
            leak = True
            chars.append(source[index + 1])
            index += 2
            continue
        if mark == '"' and char == '"':
            return "".join(chars), leak or "$" in "".join(chars), index + 1
        if char == "$":
            leak = True
        chars.append(char)
        index += 1
    return None


def _name_and_args(argv: list[str]) -> tuple[str, list[str]] | None:
    items = list(argv)
    if items and items[0] == "&":
        items = items[1:]
    if len(items) >= 3 and items[0].startswith("$") and items[1] == "=":
        items = items[2:]
    if not items:
        return None
    name = items[0]
    if name in {"&", "=", ".", ","} or name.startswith(("$", "{", "(", "@")):
        return None
    return name, items[1:]


def _secret_reason(token: str) -> str | None:
    text = (token or "").replace("/", "\\")
    lower = text.lower()
    name = lower.rsplit("\\", 1)[-1]
    if name in {".env", "deepseek-key.txt"} or name.startswith(".env."):
        return "拒绝读取密钥文件" if "key" in name else "拒绝读取环境文件"
    if "\\backend\\.env" in lower or lower.endswith("\\backend\\.env"):
        return "拒绝读取环境文件"
    if "\\账号密码" in text or text.startswith("账号密码\\") or text.startswith("账号密码/"):
        return "拒绝读取密钥文件"
    return None


def _recurse(args: list[str]) -> bool:
    for arg in args:
        head = arg.lower().split(":", 1)[0]
        if head in {"-recurse", "-r", "-rf", "/s"} or head.startswith("-recurse"):
            return True
    return False


def _targets(args: list[str]) -> list[str]:
    return [arg for arg in args if arg not in {",", "=", "&"} and not _is_flag(arg)]


def _wipe_reason(name: str, args: list[str]) -> str | None:
    bare = _bare(name)
    if bare in {"format", "format.com"} or name == "format.com":
        if any(_DRIVE_ROOT.fullmatch(arg.replace("/", "\\")) or re.fullmatch(r"[a-z]:", arg, re.IGNORECASE) for arg in args):
            return "拒绝执行磁盘格式化"
        return None
    if bare == "format-volume":
        return "拒绝执行磁盘格式化"
    if bare in {"shutdown", "restart-computer", "stop-computer", "diskpart"}:
        return "拒绝执行关机或磁盘分区命令"
    if bare == "clear-disk":
        return "拒绝执行破坏磁盘的命令"
    if bare == "cipher" and any(arg.lower().startswith("/w") for arg in args):
        return "拒绝执行破坏磁盘的命令"
    if bare == "remove-item" and _recurse(args):
        for target in _targets(args):
            folded = target.replace("/", "\\")
            if folded in {"/", "\\"}:
                return "拒绝删除根目录"
            if _DRIVE_ROOT.fullmatch(folded):
                return "拒绝递归删除盘符根目录"
            if folded.lower() == "c:\\windows" or folded.lower().startswith("c:\\windows\\"):
                return "拒绝递归删除 Windows 目录"
    return None


def _shell_payload(name: str, args: list[str]) -> str | None:
    bare = _bare(name)
    if bare not in _SHELLS:
        return None
    take = False
    payload: list[str] = []
    for arg in args:
        head = arg.lower().split(":", 1)[0]
        if bare in {"powershell", "pwsh"} and (_ENCODED.fullmatch(head) or head.startswith("-encoded")):
            return "\0encoded"
        if take:
            payload.append(arg)
            continue
        if head in {"/c", "/k", "-c", "-command", "-file"}:
            take = True
            if ":" in arg and head in {"-command", "-file"}:
                payload.append(arg.split(":", 1)[1])
            continue
    if not take:
        return None
    return " ".join(payload)


def _comma_groups(argv: list[str]) -> list[list[str]]:
    groups: list[list[str]] = [[]]
    for token in argv:
        if token == ",":
            groups.append([])
            continue
        groups[-1].append(token)
    return [group for group in groups if group]


def _block_argv(argv: list[str], depth: int) -> str | None:
    named = _name_and_args(argv)
    if named is None:
        for token in argv:
            secret = _secret_reason(token)
            if secret:
                return secret
        return None
    name, args = named
    resolved = canonical(name)
    for token in [name, *args]:
        secret = _secret_reason(token)
        if secret:
            return secret
        if ":" in token:
            secret = _secret_reason(token.split(":", 1)[1])
            if secret:
                return secret
    reason = _wipe_reason(resolved, args)
    if reason:
        return reason
    if resolved == "start-process":
        for arg in args:
            launched = _bare(canonical(arg))
            if launched in {"format", "format-volume", "shutdown", "restart-computer", "stop-computer", "diskpart", "clear-disk"}:
                return _wipe_reason(launched, args) or "拒绝执行关机或磁盘分区命令"
    payload = _shell_payload(name, args)
    if payload == "\0encoded":
        return "拒绝执行编码后的命令"
    if payload:
        return command_block_reason(payload, depth + 1)
    return None


def command_block_reason(command: str, depth: int = 0) -> str | None:
    if depth > 3:
        return "拒绝执行嵌套后无法确认的命令"
    parsed = parse_commands(command)
    if parsed is None:
        return _fallback_block(command)
    for cmd in parsed:
        for group in _comma_groups(cmd.argv):
            reason = _block_argv(group, depth)
            if reason:
                return reason
    return None


def _fallback_block(command: str) -> str | None:
    visible: list[str] = []
    index = 0
    text = command or ""
    while index < len(text):
        char = text[index]
        if char == "#":
            while index < len(text) and text[index] != "\n":
                index += 1
            continue
        if char in "'\"":
            quoted = _read_quote(text, index, char)
            if quoted is None:
                break
            _body, _leak, index = quoted
            visible.append(" ")
            continue
        visible.append(char)
        index += 1
    plain = "".join(visible).lower()
    if re.search(r"(?:^|[;&|\n])\s*format(?:\.com)?\s+[a-z]:", plain):
        return "拒绝执行磁盘格式化"
    if re.search(r"(?:^|[;&|\n])\s*format-volume\b", plain):
        return "拒绝执行磁盘格式化"
    if re.search(r"(?:^|[;&|\n])\s*(shutdown|restart-computer|stop-computer|diskpart)\b", plain):
        return "拒绝执行关机或磁盘分区命令"
    if re.search(r"(?:^|[;&|\n])\s*clear-disk\b", plain) or "cipher /w" in plain:
        return "拒绝执行破坏磁盘的命令"
    return None


def _flag_ok(spec: dict, token: str) -> bool:
    raw = token.lower()
    head, _, value = raw.partition(":")
    if value and _leaky(value):
        return False
    if spec.get("all_flags"):
        return True
    flags = spec.get("flags") or set()
    if head in flags:
        return True
    if re.fullmatch(r"-[a-z]{2,}", head) and flags and all(len(item) == 2 for item in flags):
        return all(f"-{char}" in flags for char in head[1:])
    matches = [item for item in flags if item.startswith(head)]
    return len(matches) == 1 and len(head) >= 2


def _git_readonly(args: list[str]) -> bool:
    if not args:
        return False
    sub = args[0].lower()
    if sub not in _GIT_READ:
        return False
    if sub == "branch" and any(re.fullmatch(r"-[dD]", arg) or arg.lower() in {"-d", "-delete"} for arg in args[1:]):
        return False
    return not any(_leaky(arg) for arg in args)


def _segment_readonly(cmd: _Cmd) -> bool:
    if cmd.unsafe_redir or not cmd.argv:
        return False
    if any(cmd.leaks):
        return False
    named = _name_and_args(cmd.argv)
    if named is None:
        return False
    name, args = named
    resolved = canonical(name)
    bare = _bare(resolved)
    if bare in _SHELLS or resolved in {"powershell.exe", "pwsh.exe", "cmd.exe"}:
        return False
    if resolved == "git":
        return _git_readonly(args)
    spec = _ALLOW.get(resolved) or _ALLOW.get(bare)
    if spec is None:
        return False
    if spec.get("verb"):
        verb = next((arg for arg in args if not _is_flag(arg) and arg != ","), "")
        if verb.lower() != spec["verb"]:
            return False
    after_comma = False
    for arg in args:
        if arg == ",":
            after_comma = True
            continue
        if _leaky(arg):
            return False
        if _is_flag(arg):
            if not _flag_ok(spec, arg):
                return False
            after_comma = False
            continue
        if spec.get("no_positional"):
            return False
        if after_comma and (canonical(arg) in _KNOWN or _bare(canonical(arg)) in _KNOWN):
            return False
        after_comma = False
    return True


def command_is_readonly(command: str) -> bool:
    parsed = parse_commands(command)
    if not parsed:
        return False
    return all(_segment_readonly(cmd) for cmd in parsed)
