"""Wwise object type wrappers for the hierarchy model."""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import IntEnum


class HircType(IntEnum):
    SETTINGS = 1
    SOUND = 2
    ACTION = 3
    EVENT = 4
    RANDOM_SEQUENCE = 5
    SWITCH_CONTAINER = 6
    ACTOR_MIXER = 7
    BUS = 8
    BLEND_CONTAINER = 9
    MUSIC_SEGMENT = 10
    MUSIC_TRACK = 11
    MUSIC_SWITCH = 12
    MUSIC_PLAYLIST = 13

    def label(self) -> str:
        return {
            1: "Settings", 2: "Sound", 3: "Action", 4: "Event",
            5: "Random/Seq Container", 6: "Switch Container", 7: "Actor-Mixer",
            8: "Bus", 9: "Blend Container", 10: "Music Segment",
            11: "Music Track", 12: "Music Switch", 13: "Music Playlist",
        }.get(self.value, f"Unknown({self.value})")

    def icon_key(self) -> str:
        return {
            1: "settings", 2: "sound", 3: "action", 4: "event",
            5: "container", 6: "switch", 7: "mixer", 8: "bus",
            9: "blend", 10: "music", 11: "track", 12: "music_switch",
            13: "playlist",
        }.get(self.value, "unknown")


class ModState(IntEnum):
    ORIGINAL = 0
    MODIFIED = 1
    ADDED = 2
    REMOVED = 3

    def label(self) -> str:
        return {0: "", 1: "[Modified]", 2: "[Added]", 3: "[Removed]"}[self.value]


@dataclass
class HircObject:
    """Base Wwise HIRC object."""
    type_id: int
    object_id: int
    raw_payload: bytes
    parent_id: int = 0
    bus_id: int = 0
    child_ids: List[int] = field(default_factory=list)
    source_file: str = ""
    file_offset: int = 0
    mod_state: ModState = ModState.ORIGINAL
    resolved_name: str = ""

    @property
    def type_name(self) -> str:
        try:
            return HircType(self.type_id).label()
        except ValueError:
            return f"Unknown({self.type_id})"

    @property
    def display_name(self) -> str:
        if self.resolved_name:
            return self.resolved_name
        return f"{self.type_name} [{self.object_id}]"


@dataclass
class WwiseSound(HircObject):
    media_id: int = 0
    media_size: int = 0
    source_type: int = 0  # 0=embedded, 1=streamed, 2=prefetched
    plugin_id: int = 0


@dataclass
class WwiseAction(HircObject):
    action_type: int = 0
    target_id: int = 0


@dataclass
class WwiseEvent(HircObject):
    action_ids: List[int] = field(default_factory=list)


@dataclass
class WwiseRandomContainer(HircObject):
    pass


@dataclass
class WwiseSwitchContainer(HircObject):
    pass


@dataclass
class WwiseActorMixer(HircObject):
    pass


@dataclass
class WwiseBlendContainer(HircObject):
    pass


@dataclass
class WwiseMusicSegment(HircObject):
    pass


@dataclass
class WwiseMusicTrack(HircObject):
    media_id: int = 0
    media_size: int = 0
    track_sources: List[tuple] = field(default_factory=list)


@dataclass
class WwiseMusicSwitch(HircObject):
    pass


@dataclass
class WwiseMusicPlaylist(HircObject):
    pass


def create_object(type_id: int, object_id: int, raw_payload: bytes,
                  **kwargs) -> HircObject:
    """Factory to create the appropriate HircObject subclass."""
    cls_map = {
        2: WwiseSound, 3: WwiseAction, 4: WwiseEvent,
        5: WwiseRandomContainer, 6: WwiseSwitchContainer,
        7: WwiseActorMixer, 9: WwiseBlendContainer,
        10: WwiseMusicSegment, 11: WwiseMusicTrack,
        12: WwiseMusicSwitch, 13: WwiseMusicPlaylist,
    }
    cls = cls_map.get(type_id, HircObject)
    return cls(type_id=type_id, object_id=object_id,
               raw_payload=raw_payload, **kwargs)
