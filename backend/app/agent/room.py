"""群聊房间状态机，对齐 AutoGen SwarmGroupChatManager。

下一位只由最后一条 HandoffMessage 的 target 决定。没有交接时仍是当前发言者。
交给 user 时本轮结束。轮数达到 max_turns 时结束。
"""

from dataclasses import dataclass, field

USER = "user"
USER_NAMES = {"user", "用户"}


@dataclass
class RoomMessage:
    source: str
    content: str
    target: str = ""
    kind: str = "text"


@dataclass
class SwarmRoom:
    participants: list[str]
    max_turns: int | None = 6
    current_speaker: str = ""
    current_turn: int = 0
    thread: list[RoomMessage] = field(default_factory=list)
    terminated: bool = False
    stop_reason: str = ""

    def __post_init__(self) -> None:
        names = [str(name).strip() for name in self.participants if str(name).strip()]
        if len(names) < 2:
            raise ValueError("群聊至少要有两个成员")
        if len(set(names)) != len(names):
            raise ValueError("成员名字不能重复")
        if any(name in USER_NAMES for name in names):
            raise ValueError("成员不能叫 user")
        self.participants = names
        if not self.current_speaker:
            self.current_speaker = names[0]
        if self.current_speaker not in self.participants:
            raise ValueError(f"当前发言者不在成员里: {self.current_speaker}")

    def validate_group_state(self, messages: list[RoomMessage] | None) -> None:
        """对齐 SwarmGroupChatManager.validate_group_state。"""
        if messages:
            for message in messages:
                if message.kind == "handoff":
                    if message.target not in self.participants:
                        raise ValueError(
                            f"交接目标 {message.target} 不是成员 {self.participants}。"
                            "继续一轮时，新的交接目标必须是其中一位成员。"
                        )
                    return
        for existing in reversed(self.thread):
            if existing.kind == "handoff":
                if existing.target not in self.participants:
                    raise ValueError(
                        f"已有的交接目标 {existing.target} 不是成员 {self.participants}。"
                        "继续任务时要带一条指向有效成员的交接。"
                    )
                return

    def select_speaker(self) -> str:
        """对齐 SwarmGroupChatManager.select_speaker，只返回一个人。"""
        if not self.thread:
            return self.current_speaker
        for message in reversed(self.thread):
            if message.kind == "handoff":
                if message.target not in self.participants:
                    raise ValueError(f"交接目标 {message.target} 不是成员 {self.participants}")
                self.current_speaker = message.target
                return self.current_speaker
        return self.current_speaker

    def _stop(self, reason: str) -> None:
        self.terminated = True
        self.stop_reason = reason
        self.current_turn = 0

    def apply_termination(self, delta: list[RoomMessage], increment: bool) -> bool:
        """对齐 BaseGroupChatManager._apply_termination_condition 的轮数上限，并在交给 user 时结束。"""
        for message in delta:
            if message.kind == "handoff" and message.target in USER_NAMES:
                self._stop("交接给用户")
                return True
        if increment:
            self.current_turn += 1
        if self.max_turns is not None and self.current_turn >= self.max_turns:
            self._stop(f"Maximum number of turns {self.max_turns} reached.")
            return True
        return False

    def start(self, messages: list[RoomMessage]) -> str | None:
        self.validate_group_state(messages)
        self.thread.extend(messages or [])
        if self.apply_termination(messages or [], increment=False):
            return None
        return self.select_speaker()

    def observe(self, messages: list[RoomMessage]) -> str | None:
        self.thread.extend(messages or [])
        if self.apply_termination(messages or [], increment=True):
            return None
        return self.select_speaker()

    def state(self) -> dict:
        return {
            "current_speaker": self.current_speaker,
            "current_turn": self.current_turn,
            "max_turns": self.max_turns,
        }
