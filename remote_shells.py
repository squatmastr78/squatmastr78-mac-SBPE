import json
import logging
import os
import threading
import time
import threading
import time

from _remote import ffi, lib

ORIG_CLEAR = None
ORIG_PRESENT = None
HOOKS = []
HIDDEN = []
STAGE = ffi.NULL


def get_class_name(obj):
    if obj == ffi.NULL:
        return "NULL"

    classptr = ffi.cast("void****", obj)[0]
    nameptr = classptr[-1][1]
    name = ffi.string(ffi.cast("char *", nameptr), 100)

    return name[1 if len(name) < 11 else 2:].decode(
        "utf-8", errors="replace"
    )


def get_allies():
    if STAGE == ffi.NULL or STAGE[0] == ffi.NULL:
        return []

    children = STAGE[0].asUIElementContainer.children

    start = int(ffi.cast("uintptr_t", children.start))
    finish = int(ffi.cast("uintptr_t", children.finish))

    if start == 0 or finish <= start:
        return []

    first = ffi.cast("struct UIElement **", children.start)[0]

    if first == ffi.NULL:
        return []

    # On the menu the first child is not GameClient.
    if get_class_name(first) != "GameClient":
        return []

    gc = ffi.cast("struct GameClient *", first)

    if gc.worldClient == ffi.NULL:
        return []

    cw = gc.worldClient.clientWorld

    if cw == ffi.NULL:
        return []

    allies = cw.allies

    start = int(ffi.cast("uintptr_t", allies.start))
    finish = int(ffi.cast("uintptr_t", allies.finish))
    end = int(ffi.cast("uintptr_t", allies.endOfStorage))

    if start == 0 or finish <= start or end < finish:
        return []

    ptr_size = ffi.sizeof("struct WorldObject *")
    count = (finish - start) // ptr_size

    # Safety check against a bad/corrupted vector.
    if count < 0 or count > 128:
        return []

    return ffi.unpack(
        ffi.cast("struct WorldObject **", allies.start),
        count
    )


def restore_shells():
    global HIDDEN

    for obj, old_draw in HIDDEN:
        try:
            if obj != ffi.NULL:
                obj.props.draw = old_draw
        except Exception:
            pass

    HIDDEN = []


@ffi.def_extern()
def hook_Clear(color):
    ORIG_CLEAR(color)


@ffi.def_extern()
def hook_Present():
    ORIG_PRESENT()


def install_hook(address, callback, ctype):
    hook = lib.subhook_new(address, callback, 1)

    if hook == ffi.NULL:
        raise RuntimeError("subhook_new failed")

    trampoline = lib.subhook_get_trampoline(hook)

    if trampoline != ffi.NULL:
        original = ffi.cast(ctype, trampoline)
        logging.info("trampoline created")
    else:
        logging.info("no trampoline; using remove/call/reinstall fallback")

        direct_original = ffi.cast(ctype, address)

        def original(*args):
            lib.subhook_remove(hook)
            try:
                return direct_original(*args)
            finally:
                lib.subhook_install(hook)

    if lib.subhook_install(hook) != 0:
        raise RuntimeError("subhook_install failed")

    if not lib.subhook_is_installed(hook):
        raise RuntimeError("hook installation failed")

    HOOKS.append(hook)

    return original


FRAME_HIDE_ENABLED = True
FRAME_HIDDEN = []

GLCLEAR_CALLBACK = None
GLCLEAR_ORIGINAL = None
GLCLEAR_SLOT_PTR = None

SWAP_CALLBACK = None
SWAP_ORIGINAL = None
SWAP_SLOT_PTR = None

OFFSET = 1 << 29



EXTRA_HIDDEN = []


def get_client_world_for_extra():
    if STAGE == ffi.NULL or STAGE[0] == ffi.NULL:
        return ffi.NULL

    children = STAGE[0].asUIElementContainer.children

    start = int(ffi.cast("uintptr_t", children.start))
    finish = int(ffi.cast("uintptr_t", children.finish))

    if start == 0 or finish <= start:
        return ffi.NULL

    first = ffi.cast(
        "struct UIElement **",
        children.start
    )[0]

    if first == ffi.NULL:
        return ffi.NULL

    if get_class_name(first) != "GameClient":
        return ffi.NULL

    gc = ffi.cast("struct GameClient *", first)

    if gc.worldClient == ffi.NULL:
        return ffi.NULL

    cw = gc.worldClient.clientWorld

    if cw == ffi.NULL:
        return ffi.NULL

    return cw


def vec2list_extra(vector, itemtype):
    start = int(ffi.cast("uintptr_t", vector.start))
    finish = int(ffi.cast("uintptr_t", vector.finish))
    end = int(ffi.cast("uintptr_t", vector.endOfStorage))

    if start == 0 or finish < start or end < finish:
        return []

    item_size = ffi.sizeof(itemtype)

    if item_size <= 0:
        return []

    span = finish - start

    if span % item_size != 0:
        return []

    count = span // item_size

    if count < 0 or count > 8192:
        return []

    return ffi.unpack(
        ffi.cast(itemtype + " *", vector.start),
        count
    )


def svecmap2list_extra(svecmap, itemtype):
    elems = vec2list_extra(
        svecmap.vec,
        "struct SortedVecElement"
    )

    result = []

    for elem in elems:
        if elem.obj != ffi.NULL:
            result.append(
                ffi.cast(itemtype, elem.obj)
            )

    return result


def worldobjects_extra(subworld):
    if subworld == ffi.NULL:
        return []

    return svecmap2list_extra(
        subworld.asSubWorldImpl.objs,
        "struct WorldObject *"
    )



def mac_vid_is_timer(stdstr):
    """
    Read only the inline bytes of the Mac std::string object.

    IMPORTANT:
    We deliberately do NOT use stdstr.s and do NOT dereference
    a candidate heap pointer. That was what caused the SIGSEGV.
    """
    if stdstr == ffi.NULL:
        return False

    try:
        raw = ffi.cast("unsigned char *", stdstr)

        # Normal little-endian libc++ short-string layout:
        # byte 0 = length << 1, characters begin at byte 1.
        tag0 = int(raw[0])

        if (tag0 & 1) == 0:
            length = tag0 >> 1

            if 5 <= length <= 22:
                prefix = bytes(
                    ffi.buffer(
                        ffi.cast("char *", raw + 1),
                        5
                    )
                )

                if prefix == b"timer":
                    return True

        # Also tolerate libc++'s alternate short-string layout:
        # characters begin at byte 0 and size/tag is at byte 23.
        tag23 = int(raw[23])

        if (tag23 & 0x80) == 0:
            length = tag23 & 0x7f

            if 5 <= length <= 22:
                prefix = bytes(
                    ffi.buffer(
                        ffi.cast("char *", raw),
                        5
                    )
                )

                if prefix == b"timer":
                    return True

        return False

    except Exception:
        return False


def hide_frame_extra_objects():
    global EXTRA_HIDDEN

    if not FRAME_HIDE_ENABLED:
        return 0

    # Never subtract twice before the swap callback.
    if EXTRA_HIDDEN:
        return len(EXTRA_HIDDEN)

    try:
        cw = get_client_world_for_extra()

        if cw == ffi.NULL:
            return 0

        allies = get_allies()

        shell_addresses = set()

        for shell in allies:
            if shell != ffi.NULL:
                shell_addresses.add(
                    int(ffi.cast("uintptr_t", shell))
                )

        worlds = svecmap2list_extra(
            cw.clientSubWorlds,
            "struct ForeignSubWorld *"
        )

        seen = set()

        for csubworld in worlds:
            if csubworld == ffi.NULL:
                continue

            for obj in worldobjects_extra(csubworld):
                if obj == ffi.NULL:
                    continue

                addr = int(
                    ffi.cast("uintptr_t", obj)
                )

                if addr in seen:
                    continue

                seen.add(addr)

                # Shells are already handled by our proven shell hider.
                if addr in shell_addresses:
                    continue

                # Same exclusion as Windows SBPE.
                if int(obj.props.terraintype) > 0:
                    continue

                # Preserve Fabricator/etc. countdown timers.
                # Use our Mac-safe inline string check instead of
                # Windows SBPE's unsafe ffi.string(stdstr.s).
                if mac_vid_is_timer(obj.props.vid):
                    continue

                obj.props.xmp = (
                    int(obj.props.xmp) - OFFSET
                )

                EXTRA_HIDDEN.append(obj)

        return len(EXTRA_HIDDEN)

    except Exception:
        logging.exception(
            "frame extra-object hide failed"
        )
        return len(EXTRA_HIDDEN)


def restore_frame_extra_objects():
    global EXTRA_HIDDEN

    if not EXTRA_HIDDEN:
        return 0

    hidden = EXTRA_HIDDEN
    EXTRA_HIDDEN = []

    restored = 0

    try:
        for obj in hidden:
            if obj == ffi.NULL:
                continue

            # IMPORTANT:
            # Match Windows SBPE exactly here.
            # Always add OFFSET back instead of checking
            # whether the object moved during the frame.
            obj.props.xmp = (
                int(obj.props.xmp) + OFFSET
            )

            restored += 1

    except Exception:
        logging.exception(
            "frame extra-object restore failed"
        )

    return restored



CENTER_CAMERA_ENABLED = True
CENTER_CAMERA_LOGGED = False


def get_world_client_for_center():
    if STAGE == ffi.NULL or STAGE[0] == ffi.NULL:
        return ffi.NULL

    children = STAGE[0].asUIElementContainer.children

    start = int(ffi.cast("uintptr_t", children.start))
    finish = int(ffi.cast("uintptr_t", children.finish))

    if start == 0 or finish <= start:
        return ffi.NULL

    first = ffi.cast(
        "struct UIElement **",
        children.start
    )[0]

    if first == ffi.NULL:
        return ffi.NULL

    if get_class_name(first) != "GameClient":
        return ffi.NULL

    gc = ffi.cast("struct GameClient *", first)

    if gc.worldClient == ffi.NULL:
        return ffi.NULL

    return gc.worldClient


def apply_center_camera():
    global CENTER_CAMERA_LOGGED

    try:
        wc = get_world_client_for_center()

        if wc == ffi.NULL:
            return False

        wv = wc.worldView

        if wv == ffi.NULL:
            return False

        BN = 1000000

        if CENTER_CAMERA_ENABLED:
            # Windows SBPE mode 2:
            # continuously force StarBreak to recalculate
            # the camera and allow it outside world bounds.
            wv.offsetsInitialized = False

            if int(wv.playerBounds.w) < int(wv.worldBounds.w) + BN:
                wv.playerBounds.x = int(wv.playerBounds.x) - BN
                wv.playerBounds.y = int(wv.playerBounds.y) - BN
                wv.playerBounds.w = int(wv.playerBounds.w) + BN * 2
                wv.playerBounds.h = int(wv.playerBounds.h) + BN * 2

                logging.info(
                    "CENTER MODE 2 expanded playerBounds "
                    "x=%d y=%d w=%d h=%d",
                    int(wv.playerBounds.x),
                    int(wv.playerBounds.y),
                    int(wv.playerBounds.w),
                    int(wv.playerBounds.h)
                )

            if not CENTER_CAMERA_LOGGED:
                CENTER_CAMERA_LOGGED = True

                logging.info(
                    "CENTER CAMERA ON"
                )

            return True

        # Centering has been switched OFF.
        #
        # Undo the same giant playerBounds expansion,
        # exactly like Windows SBPE does when leaving mode 2.
        if int(wv.playerBounds.w) > int(wv.worldBounds.w) + BN:
            wv.playerBounds.x = int(wv.playerBounds.x) + BN
            wv.playerBounds.y = int(wv.playerBounds.y) + BN
            wv.playerBounds.w = int(wv.playerBounds.w) - BN * 2
            wv.playerBounds.h = int(wv.playerBounds.h) - BN * 2

            # Force StarBreak to rebuild its normal camera state.
            wv.offsetsInitialized = False

            logging.info(
                "CENTER MODE 2 playerBounds restored"
            )

        CENTER_CAMERA_LOGGED = False
        return False

    except Exception:
        logging.exception("center camera failed")
        return False



def hide_frame_shells():
    global FRAME_HIDDEN

    if not FRAME_HIDE_ENABLED:
        return 0

    # If glClear happens more than once before the swap,
    # never subtract OFFSET twice.
    if FRAME_HIDDEN:
        return len(FRAME_HIDDEN)

    try:
        allies = get_allies()
        seen = set()

        for obj in allies:
            if obj == ffi.NULL:
                continue

            addr = int(ffi.cast("uintptr_t", obj))

            if addr in seen:
                continue

            seen.add(addr)

            real_xmp = int(obj.props.xmp)
            hidden_xmp = real_xmp - OFFSET

            obj.props.xmp = hidden_xmp

            # Keep the actual object pointer just until the
            # presentation callback later in this same frame.
            FRAME_HIDDEN.append(
                (obj, real_xmp, hidden_xmp)
            )

        return len(FRAME_HIDDEN)

    except Exception:
        logging.exception("frame shell hide failed")
        return 0


def restore_frame_shells():
    global FRAME_HIDDEN

    if not FRAME_HIDDEN:
        return 0

    hidden = FRAME_HIDDEN
    FRAME_HIDDEN = []

    restored = 0

    try:
        for obj, real_xmp, hidden_xmp in hidden:
            if obj == ffi.NULL:
                continue

            # Don't overwrite a position if the game somehow
            # changed it between hide and restore.
            if int(obj.props.xmp) == hidden_xmp:
                obj.props.xmp = real_xmp
                restored += 1

    except Exception:
        logging.exception("frame shell restore failed")

    return restored




# ============================================================
# MAC EXTRA-INFO: first real door-arrow test
# ============================================================

DRAW_TEST_RECT = None
DRAW_TEST_TRI = None
DRAW_TEST_LOGGED = False

DOOR_VIDS = set("""
cave.nextDoor cave.bossgate
city.nextDoor city.bossgate
dumb.nextDoor dumb.bossgate
forest.nextdoor forest.unextdoor forest.bossgate
hulk.door3 hulk.prevdoor hulk.hportalbase
ice.nextDoor ice.bossgate
jungle.door jungle.bossgate
lab.nextdoor lab.bossgate
sc.door
""".split())


SECRET_VIDS = set("""
cave.sd
city.sd
dumb.fw
forest.sspot forest.sdopen
hulk.sd2
jungle.sd2
lab.sd2
""".split())

SHOP_VIDS = set("""
dumb.secretspot dumb.secretspoton
hulk.sspot hulk.sdopen
jungle.sspot jungle.sdopen
lab.sspot lab.sspotdone
""".split())


HP_VIDS = set("""
loot-health1 loot-xmas
""".split())



ARCADE_VIDS = set("""
forest.miner
dumb.steletop
hulk.sg
lab.sg
""".split())



BOOST_PREFIXES = set("""
loot-maxhealth
loot-maxammo
loot-damage
loot-armor
loot-mspeed
loot-jheight
loot-critchance
loot-critmult
""".split())



# Mac x86_64 PlayerCharacter / CharacterDescription layout.
#
# Each entry:
#     VID prefix: (PlayerCharacter base-stat offset,
#                  CharacterDescription RepeatedField<int> offset)
#
# Recovered from PlayerCharacter::applyBoost().
BOOST_SPECS_MAC = {
    "loot-maxhealth":  (0x2c4, 0x28),
    "loot-maxammo":    (0x2cc, 0x38),
    "loot-damage":     (0x2dc, 0x48),
    "loot-armor":      (0x2e4, 0x60),
    "loot-mspeed":     (0x2ec, 0x70),
    "loot-jheight":    (0x2f4, 0x80),
    "loot-critchance": (0x2fc, 0x90),
    "loot-critmult":   (0x304, 0xa0),
}


def extra_info_is_boost_vid(vid):
    try:
        if not vid:
            return False

        # Windows SBPE boost VIDs end in their boost level,
        # e.g. loot-damage2.
        if len(vid) < 2:
            return False

        if not vid[-1].isdigit():
            return False

        return vid[:-1] in BOOST_PREFIXES

    except Exception:
        return False



def extra_info_boost_needed(player, vid):
    """
    Read-only Mac version of original SBPE boost-arrow logic.

    Returns True only when this boost can still raise the
    player's BASE stat.

    Does not call applyBoost and does not write game memory.
    """
    try:
        if player == ffi.NULL:
            return False

        if not extra_info_is_boost_vid(vid):
            return False

        prefix = vid[:-1]
        boost_level = int(vid[-1])

        spec = BOOST_SPECS_MAC.get(prefix)

        if spec is None:
            return False

        stat_offset, maxvals_offset = spec

        player_base = ffi.cast(
            "unsigned char *",
            player
        )

        # PlayerCharacter StatVal::base is the first int
        # at each recovered stat offset.
        current_value = int(
            ffi.cast(
                "int *",
                player_base + stat_offset
            )[0]
        )

        # PlayerCharacter::charDesc():
        #     *(player + 0x278)
        char_desc_addr = int(
            ffi.cast(
                "uintptr_t *",
                player_base + 0x278
            )[0]
        )

        if char_desc_addr == 0:
            return False

        char_desc = ffi.cast(
            "unsigned char *",
            char_desc_addr
        )

        # protobuf RepeatedField<int> layout on this build:
        #
        # +0x00  int *elements
        # +0x08  int current_size
        # +0x0c  int total_size
        #
        # This matches what PlayerCharacter::applyBoost()
        # itself reads.
        repeated = char_desc + maxvals_offset

        elements_addr = int(
            ffi.cast(
                "uintptr_t *",
                repeated
            )[0]
        )

        current_size = int(
            ffi.cast(
                "int *",
                repeated + 8
            )[0]
        )

        if elements_addr == 0:
            return False

        # Sanity check before dereferencing.
        if current_size <= 1 or current_size > 64:
            return False

        elements = ffi.cast(
            "int *",
            elements_addr
        )

        # Original Windows SBPE:
        #
        # for i in range(1, len(mvals)):
        #     if mvals[i] > currvalue and i <= blevel:
        #         draw boost arrow
        #
        # Mirror that exactly.
        limit = min(
            boost_level,
            current_size - 1
        )

        for i in range(1, limit + 1):
            if int(elements[i]) > current_value:
                return True

        return False

    except Exception:
        logging.exception(
            "EXTRA INFO boost need check failed vid=%s",
            vid
        )
        return False


def extra_info_target_kind(vid):
    # Priority order:
    # 1. secret
    # 2. shop
    # 3. door
    if vid in SECRET_VIDS:
        return "secret"

    if vid in SHOP_VIDS:
        return "shop"

    if vid in DOOR_VIDS:
        return "door"

    if vid in ARCADE_VIDS:
        return "arcade"

    if vid in HP_VIDS:
        return "hp"

    if extra_info_is_boost_vid(vid):
        return "boost"

    return None


EXTRA_INFO_ENABLED = False

TARGET_COLORS = {
    "boost": 0xffff00ff,
    "arcade": 0xffff8000,
    "door":   0xffffff00,
    "secret": 0xff0000cc,
    "shop":   0xff00ffff,
    "hp":     0xff00ff00,
}

DOOR_VIDS_LOGGED = set()
DOOR_DRAW_DEBUG_LOGGED = False


def mac_get_short_string(stdstr):
    """
    Safe Mac libc++ short-string reader.

    IMPORTANT:
    Never dereference stdstr.s like Windows SBPE does.
    That was the source of our earlier Mac crash.

    All official door VIDs are short enough to fit in
    libc++'s short-string representation.
    """
    if stdstr == ffi.NULL:
        return ""

    try:
        raw = ffi.cast(
            "unsigned char *",
            stdstr
        )

        # Layout already proven by our Fabricator timer fix:
        # first byte contains short-string length/tag,
        # characters begin at byte 1.
        tag0 = int(raw[0])

        if (tag0 & 1) == 0:
            length = tag0 >> 1

            if 0 <= length <= 22:
                data = bytes(
                    ffi.buffer(
                        ffi.cast(
                            "char *",
                            raw + 1
                        ),
                        length
                    )
                )

                try:
                    return data.decode("utf-8")
                except Exception:
                    return ""

        # Alternate libc++ short layout.
        tag23 = int(raw[23])

        if (tag23 & 0x80) == 0:
            length = tag23 & 0x7f

            if 0 <= length <= 22:
                data = bytes(
                    ffi.buffer(
                        ffi.cast(
                            "char *",
                            raw
                        ),
                        length
                    )
                )

                try:
                    return data.decode("utf-8")
                except Exception:
                    return ""

        # Long strings intentionally not dereferenced yet.
        return ""

    except Exception:
        return ""


def objects_from_sorted_vec_map(svecmap):
    """
    Mac version of SBPE util.sVecMap2list().
    Returns WorldObject pointers.
    """
    try:
        vec = svecmap.vec

        if (
            vec.start == ffi.NULL or
            vec.finish == ffi.NULL
        ):
            return []

        start = int(
            ffi.cast(
                "uintptr_t",
                vec.start
            )
        )

        finish = int(
            ffi.cast(
                "uintptr_t",
                vec.finish
            )
        )

        if finish <= start:
            return []

        elem_size = ffi.sizeof(
            "struct SortedVecElement"
        )

        count = (
            finish - start
        ) // elem_size

        # Safety limit against corrupt pointers.
        if count < 0 or count > 10000:
            return []

        elements = ffi.cast(
            "struct SortedVecElement *",
            vec.start
        )

        result = []

        for i in range(count):
            ptr = elements[i].obj

            if ptr == ffi.NULL:
                continue

            result.append(
                ffi.cast(
                    "struct WorldObject *",
                    ptr
                )
            )

        return result

    except Exception:
        logging.exception(
            "extra-info SortedVecMap scan failed"
        )
        return []


def get_extra_info_objects(cw):
    """
    Same sources Windows extra_info uses:
      serverSubWorld
      mySubWorld
      allies
    """
    objects = []

    try:
        if cw.serverSubWorld != ffi.NULL:
            objects += objects_from_sorted_vec_map(
                cw.serverSubWorld
                .asSubWorldImpl
                .objs
            )

        if cw.mySubWorld != ffi.NULL:
            objects += objects_from_sorted_vec_map(
                cw.mySubWorld
                .asNativeSubWorld
                .asSubWorldImpl
                .objs
            )

        # Windows extra_info also includes allies.
        allies = cw.allies

        if (
            allies.start != ffi.NULL and
            allies.finish != ffi.NULL
        ):
            start = int(
                ffi.cast(
                    "uintptr_t",
                    allies.start
                )
            )

            finish = int(
                ffi.cast(
                    "uintptr_t",
                    allies.finish
                )
            )

            size = ffi.sizeof(
                "struct WorldObject *"
            )

            count = (
                finish - start
            ) // size

            if 0 <= count <= 10000:
                arr = ffi.cast(
                    "struct WorldObject **",
                    allies.start
                )

                for i in range(count):
                    if arr[i] != ffi.NULL:
                        objects.append(
                            arr[i]
                        )

    except Exception:
        logging.exception(
            "extra-info object collection failed"
        )

    # Deduplicate pointers.
    result = []
    seen = set()

    for obj in objects:
        addr = int(
            ffi.cast(
                "uintptr_t",
                obj
            )
        )

        if addr in seen:
            continue

        seen.add(addr)
        result.append(obj)

    return result


def rotate_extra_info(x, y, angle):
    import math

    return (
        x * math.cos(angle)
        - y * math.sin(angle),

        x * math.sin(angle)
        + y * math.cos(angle)
    )


def draw_door_target(obj, wv, screen_w, screen_h, color, size_scale=1.0):
    import math

    global DRAW_TEST_RECT
    global DRAW_TEST_TRI
    global DOOR_DRAW_DEBUG_LOGGED

    p = obj.props

    half_w = int(p.wmp) // 512
    half_h = int(p.hmp) // 512

    target_x = (
        int(p.xmp) // 256
        + half_w
        - int(wv.offset.x)
    )

    target_y = (
        int(p.ymp) // 256
        + half_h
        - int(wv.offset.y)
    )

    inbounds = (
        target_x + half_w >= 0 and
        target_x - half_w <= screen_w and
        target_y + half_h >= 0 and
        target_y - half_h <= screen_h
    )

    if not DOOR_DRAW_DEBUG_LOGGED:
        DOOR_DRAW_DEBUG_LOGGED = True

        logging.info(
            "EXTRA INFO DRAW GEOMETRY "
            "canvas=%dx%d target=(%d,%d) "
            "offset=(%d,%d) size=(%d,%d) inbounds=%s",
            screen_w,
            screen_h,
            target_x,
            target_y,
            int(wv.offset.x),
            int(wv.offset.y),
            int(p.wmp) // 256,
            int(p.hmp) // 256,
            inbounds
        )

    # ----------------------------------------------------
    # Door is visible: draw an SBPE-style frame around it.
    # ----------------------------------------------------

    if inbounds:
        x = (
            int(p.xmp) // 256
            - int(wv.offset.x)
        )

        y = (
            int(p.ymp) // 256
            - int(wv.offset.y)
        )

        w = max(
            1,
            int(p.wmp) // 256
        )

        h = max(
            1,
            int(p.hmp) // 256
        )

        line = 3

        DRAW_TEST_RECT(
            x,
            y,
            w,
            line,
            color,
            0
        )

        DRAW_TEST_RECT(
            x,
            y + h - line,
            w,
            line,
            color,
            0
        )

        DRAW_TEST_RECT(
            x,
            y,
            line,
            h,
            color,
            0
        )

        DRAW_TEST_RECT(
            x + w - line,
            y,
            line,
            h,
            color,
            0
        )

        return

    # ----------------------------------------------------
    # Offscreen door: reproduce Windows SBPE arrow math.
    # ----------------------------------------------------

    center_x = screen_w // 2
    center_y = screen_h // 2

    angle = math.atan2(
        target_y - center_y,
        target_x - center_x
    )

    t = 1.0

    if (
        target_x < 0 and
        center_x != target_x
    ):
        t = min(
            t,
            center_x
            / (center_x - target_x)
        )

    if (
        target_y < 0 and
        center_y != target_y
    ):
        t = min(
            t,
            center_y
            / (center_y - target_y)
        )

    if (
        target_x > screen_w and
        center_x != target_x
    ):
        t = min(
            t,
            (screen_w - center_x)
            / (target_x - center_x)
        )

    if (
        target_y > screen_h and
        center_y != target_y
    ):
        t = min(
            t,
            (screen_h - center_y)
            / (target_y - center_y)
        )

    arrow_x = (
        center_x
        + t * (
            target_x - center_x
        )
    )

    arrow_y = (
        center_y
        + t * (
            target_y - center_y
        )
    )

    # Windows defaults:
    # length = 20
    # width = 10
    # 3x larger than original SBPE default
    length = 60 * size_scale
    half_width = 15 * size_scale
    bx, by = rotate_extra_info(
        -length,
        half_width,
        angle
    )

    cx, cy = rotate_extra_info(
        -length,
        -half_width,
        angle
    )

    # Bright pink for the first real test.
    DRAW_TEST_TRI(
        round(arrow_x),
        round(arrow_y),

        round(arrow_x + bx),
        round(arrow_y + by),

        round(arrow_x + cx),
        round(arrow_y + cy),

        color,
        0
    )


def draw_extra_info_test_overlay():
    global DRAW_TEST_RECT
    global DRAW_TEST_TRI
    global DRAW_TEST_LOGGED
    global DOOR_VIDS_LOGGED

    global EXTRA_INFO_ENABLED

    if not EXTRA_INFO_ENABLED:
        return

    try:
        if (
            DRAW_TEST_RECT is None or
            DRAW_TEST_TRI is None
        ):
            slide = int(
                os.environ["SBPE_SLIDE"],
                0
            )

            DRAW_TEST_RECT = ffi.cast(
                "pXDL_FillRect",
                0x1000fc5d0 + slide
            )

            DRAW_TEST_TRI = ffi.cast(
                "pXDL_FillTri",
                0x1000fca50 + slide
            )

            logging.info(
                "EXTRA INFO REAL DOOR TEST READY"
            )

        wc = get_world_client_for_center()

        if wc == ffi.NULL:
            return

        cw = wc.clientWorld
        wv = wc.worldView

        if (
            cw == ffi.NULL or
            wv == ffi.NULL
        ):
            return

        # Use StarBreak's real render-canvas dimensions.
        # Windows SBPE extra_info uses canvasW_[0]/canvasH_[0],
        # not the WorldView UI element dimensions.
        slide = int(os.environ["SBPE_SLIDE"], 0)

        canvas_w_ptr = ffi.cast(
            "int *",
            0x1001ff7c4 + slide
        )

        canvas_h_ptr = ffi.cast(
            "int *",
            0x1002177d0 + slide
        )

        screen_w = int(canvas_w_ptr[0])
        screen_h = int(canvas_h_ptr[0])

        if (
            screen_w <= 0 or
            screen_h <= 0
        ):
            return

        # Original SBPE only shows HP pickups when
        # the player is below maximum health.
        need_hp = False

        try:
            player = cw.player

            if player != ffi.NULL:
                player_obj = ffi.cast(
                    "struct WorldObject *",
                    player
                )

                current_hp = int(
                    player_obj.props.hitpoints
                )

                max_hp = int(
                    player_obj.props.maxhitpoints
                )

                need_hp = (
                    max_hp > 0
                    and current_hp < max_hp
                )

        except Exception:
            logging.exception(
                "EXTRA INFO player HP check failed"
            )

        objects = get_extra_info_objects(
            cw
        )

        door_count = 0

        for obj in objects:
            if obj == ffi.NULL:
                continue

            vid = mac_get_short_string(
                obj.props.vid
            )

            kind = extra_info_target_kind(vid)

            if kind is None:
                continue

            if kind == "hp" and not need_hp:
                continue

            if (
                kind == "boost"
                and not extra_info_boost_needed(
                    cw.player,
                    vid
                )
            ):
                continue

            door_count += 1

            if vid not in DOOR_VIDS_LOGGED:
                DOOR_VIDS_LOGGED.add(
                    vid
                )

                logging.info(
                    "EXTRA INFO TARGET FOUND kind=%s vid=%s",
                    kind,
                    vid
                )

            draw_door_target(
                obj,
                wv,
                screen_w,
                screen_h,
                TARGET_COLORS[kind],
                2.0 if kind in (
                    "shop",
                    "secret",
                    "arcade"
                ) else 1.0
            )

        if not DRAW_TEST_LOGGED:
            DRAW_TEST_LOGGED = True

            logging.info(
                "EXTRA INFO TARGET SCANNER ACTIVE"
            )

    except Exception:
        logging.exception(
            "EXTRA INFO DOOR TEST FAILED"
        )




SDL_QUIT_BLOCKER = None


def disable_sdl_quit_events():
    """
    macOS StarBreak:
    Ignore SDL_QUIT so Command-Q cannot terminate the game.
    """
    global SDL_QUIT_BLOCKER

    try:
        import ctypes

        sdl_path = os.path.expanduser(
            "~/Library/Application Support/Steam/"
            "steamapps/common/StarBreak/"
            "MVMMOClient.app/Contents/MacOS/"
            "libSDL2-2.0.0.dylib"
        )

        sdl = ctypes.CDLL(sdl_path)

        sdl.SDL_EventState.argtypes = [
            ctypes.c_uint32,
            ctypes.c_int
        ]

        sdl.SDL_EventState.restype = ctypes.c_uint8

        # SDL_QUIT   = 0x100
        # SDL_IGNORE = 0
        previous_state = sdl.SDL_EventState(
            0x100,
            0
        )

        # Keep the dylib reference alive.
        SDL_QUIT_BLOCKER = sdl

        logging.info(
            "COMMAND-Q BLOCK ACTIVE; "
            "SDL_QUIT ignored; previous_state=%d",
            int(previous_state)
        )

        return True

    except Exception:
        logging.exception(
            "COMMAND-Q blocker failed"
        )
        return False


def stage_monitor():
    global FRAME_HIDE_ENABLED
    global CENTER_CAMERA_ENABLED
    global EXTRA_INFO_ENABLED

    time.sleep(5)

    # Prevent macOS Command-Q from generating a usable
    # SDL_QUIT event.
    disable_sdl_quit_events()

    q_scancode = lib.SDL_GetScancodeFromName(b"Q")
    r_scancode = lib.SDL_GetScancodeFromName(b"R")

    # SDL2 fixed scancodes:
    # LSHIFT = 225
    # RSHIFT = 229
    lshift_scancode = 225
    rshift_scancode = 229

    previous_q = False
    previous_shift = False
    previous_r = False

    logging.info(
        "Key toggles ready: "
        "Q=%d SHIFT=(%d,%d) R=%d; "
        "EXTRA INFO starts OFF",
        q_scancode,
        lshift_scancode,
        rshift_scancode,
        r_scancode
    )

    while True:
        try:
            keyboard = lib.SDL_GetKeyboardState(
                ffi.NULL
            )

            q_pressed = False
            shift_pressed = False
            r_pressed = False

            if keyboard != ffi.NULL:
                if q_scancode > 0:
                    q_pressed = bool(
                        keyboard[q_scancode]
                    )

                shift_pressed = bool(
                    keyboard[lshift_scancode]
                    or keyboard[rshift_scancode]
                )

                if r_scancode > 0:
                    r_pressed = bool(
                        keyboard[r_scancode]
                    )

            # Q = shells / ally-noise toggle
            if q_pressed and not previous_q:
                FRAME_HIDE_ENABLED = (
                    not FRAME_HIDE_ENABLED
                )

                if FRAME_HIDE_ENABLED:
                    logging.info(
                        "Q pressed -> "
                        "FRAME SHELLS HIDDEN"
                    )
                else:
                    restore_frame_extra_objects()
                    restored = restore_frame_shells()

                    logging.info(
                        "Q pressed -> "
                        "FRAME SHELLS VISIBLE; "
                        "restored=%d",
                        restored
                    )

            # Either Shift = centered camera toggle
            if (
                shift_pressed
                and not previous_shift
            ):
                CENTER_CAMERA_ENABLED = (
                    not CENTER_CAMERA_ENABLED
                )

                if CENTER_CAMERA_ENABLED:
                    logging.info(
                        "SHIFT pressed -> "
                        "CENTER CAMERA ON"
                    )
                else:
                    logging.info(
                        "SHIFT pressed -> "
                        "CENTER CAMERA OFF"
                    )

            # R = Extra Info ON/OFF
            if r_pressed and not previous_r:
                EXTRA_INFO_ENABLED = (
                    not EXTRA_INFO_ENABLED
                )

                if EXTRA_INFO_ENABLED:
                    logging.info(
                        "R pressed -> EXTRA INFO ON"
                    )
                else:
                    logging.info(
                        "R pressed -> EXTRA INFO OFF"
                    )

            previous_q = q_pressed
            previous_shift = shift_pressed
            previous_r = r_pressed

        except Exception:
            logging.exception(
                "keyboard toggle monitor failed"
            )

        time.sleep(0.02)




PRESENT_CALLSITE_CALLBACK = None
PRESENT_CALLSITE_ORIGINAL = None


def install_present_callsite_hook():
    global PRESENT_CALLSITE_CALLBACK
    global PRESENT_CALLSITE_ORIGINAL

    try:
        import ctypes
        import struct

        slide = int(
            os.environ["SBPE_SLIDE"],
            0
        )

        # Exact Stage::Run instruction found with otool:
        #
        # 0x100075f2c  callq XDL_Present
        callsite = 0x100075f2c + slide

        # Real XDL_Present implementation.
        present_addr = 0x1000fccd0 + slide

        code = ffi.cast(
            "unsigned char *",
            callsite
        )

        # x86-64 CALL rel32 opcode.
        if int(code[0]) != 0xE8:
            logging.error(
                "PRESENT HOOK wrong opcode=%s",
                hex(int(code[0]))
            )
            return False

        old_disp = struct.unpack(
            "<i",
            bytes(ffi.buffer(code + 1, 4))
        )[0]

        old_target = (
            callsite
            + 5
            + old_disp
        )

        logging.info(
            "PRESENT CALLSITE call=%s target=%s expected=%s",
            hex(callsite),
            hex(old_target),
            hex(present_addr)
        )

        if old_target != present_addr:
            logging.error(
                "PRESENT CALLSITE target mismatch"
            )
            return False

        original_present = ffi.cast(
            "void(*)(void)",
            present_addr
        )

        first_hit = [False]

        @ffi.callback("void(void)")
        def present_callback():

            # This is our Windows-SBPE-style onPresent point:
            #
            # world/UI drawing is complete
            # but XDL_Present has NOT happened yet.

            restore_frame_extra_objects()
            restore_frame_shells()

            draw_extra_info_test_overlay()

            if not first_hit[0]:
                first_hit[0] = True

                logging.info(
                    "PRE-XDL PRESENT CALLBACK ACTIVE"
                )

            original_present()

        callback_addr = int(
            ffi.cast(
                "uintptr_t",
                present_callback
            )
        )

        new_disp = (
            callback_addr
            - (callsite + 5)
        )

        if not (
            -(1 << 31)
            <= new_disp
            < (1 << 31)
        ):
            logging.error(
                "PRESENT CALLBACK OUT OF RANGE delta=%s",
                hex(new_disp)
            )
            return False

        logging.info(
            "PRESENT patch call=%s callback=%s delta=%s",
            hex(callsite),
            hex(callback_addr),
            hex(new_disp)
        )

        # Keep Python/CFFI objects alive.
        PRESENT_CALLSITE_CALLBACK = present_callback
        PRESENT_CALLSITE_ORIGINAL = original_present

        page_size = os.sysconf(
            "SC_PAGE_SIZE"
        )

        page_start = (
            callsite
            & ~(page_size - 1)
        )

        libc = ctypes.CDLL(
            None,
            use_errno=True
        )

        libc.mprotect.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_int
        ]

        libc.mprotect.restype = ctypes.c_int

        # READ | WRITE | EXECUTE
        result = libc.mprotect(
            ctypes.c_void_p(page_start),
            page_size,
            7
        )

        if result != 0:
            logging.error(
                "PRESENT mprotect RWX failed errno=%d",
                ctypes.get_errno()
            )
            return False

        # Keep the E8 CALL opcode.
        # Only replace its rel32 destination.
        ffi.buffer(
            code + 1,
            4
        )[:] = struct.pack(
            "<i",
            new_disp
        )

        # Restore READ | EXECUTE.
        libc.mprotect(
            ctypes.c_void_p(page_start),
            page_size,
            5
        )

        # Verify what is now encoded.
        verify_disp = struct.unpack(
            "<i",
            bytes(ffi.buffer(code + 1, 4))
        )[0]

        verify_target = (
            callsite
            + 5
            + verify_disp
        )

        if verify_target != callback_addr:
            logging.error(
                "PRESENT PATCH verification failed "
                "target=%s callback=%s",
                hex(verify_target),
                hex(callback_addr)
            )
            return False

        logging.info(
            "PRESENT CALLSITE HOOK INSTALLED target=%s",
            hex(verify_target)
        )

        return True

    except Exception:
        logging.exception(
            "present callsite hook failed"
        )
        return False


def glclear_slot_probe():
    # Install pre-present interception immediately.
    install_present_callsite_hook()

    # Existing glClear / SDL swap hooks still install normally.
    time.sleep(8)

    try:
        slide = int(os.environ["SBPE_SLIDE"], 0)

        glclear_slot_addr = 0x1001e2ea8 + slide
        swap_slot_addr = 0x1001e2cd0 + slide

        glclear_slot = ffi.cast(
            "uintptr_t *",
            glclear_slot_addr
        )

        swap_slot = ffi.cast(
            "uintptr_t *",
            swap_slot_addr
        )

        logging.info(
            "FRAME SLOTS glclear=%s swap=%s",
            hex(int(glclear_slot[0])),
            hex(int(swap_slot[0]))
        )

    except Exception:
        logging.exception("frame slot probe failed")


def glclear_passthrough_test():
    global GLCLEAR_CALLBACK
    global GLCLEAR_ORIGINAL
    global GLCLEAR_SLOT_PTR

    time.sleep(12)

    try:
        slide = int(os.environ["SBPE_SLIDE"], 0)

        slot_addr = 0x1001e2ea8 + slide
        slot = ffi.cast("uintptr_t *", slot_addr)

        original_addr = int(slot[0])

        original = ffi.cast(
            "void(*)(unsigned int)",
            original_addr
        )

        first_hit = [False]
        last_count = [-1]

        @ffi.callback("void(unsigned int)")
        def glclear_callback(mask):
            apply_center_camera()
            hide_frame_extra_objects()
            count = hide_frame_shells()

            if not first_hit[0]:
                first_hit[0] = True

                logging.info(
                    "FRAME GLCLEAR CALLBACK ACTIVE mask=%s",
                    hex(int(mask))
                )

            if FRAME_HIDE_ENABLED and count != last_count[0]:
                logging.info(
                    "FRAME HIDE active; shells=%d",
                    count
                )
                last_count[0] = count

            # SBPE hides before XDL_Clear runs.
            original(mask)

        GLCLEAR_CALLBACK = glclear_callback
        GLCLEAR_ORIGINAL = original
        GLCLEAR_SLOT_PTR = slot

        callback_addr = int(
            ffi.cast("uintptr_t", glclear_callback)
        )

        logging.info(
            "FRAME GLCLEAR installing slot=%s original=%s callback=%s",
            hex(slot_addr),
            hex(original_addr),
            hex(callback_addr)
        )

        slot[0] = callback_addr

        logging.info("FRAME GLCLEAR INSTALLED")

    except Exception:
        logging.exception("frame glClear hook failed")


def swap_passthrough_test():
    global SWAP_CALLBACK
    global SWAP_ORIGINAL
    global SWAP_SLOT_PTR

    time.sleep(12)

    try:
        slide = int(os.environ["SBPE_SLIDE"], 0)

        slot_addr = 0x1001e2cd0 + slide
        slot = ffi.cast("uintptr_t *", slot_addr)

        original_addr = int(slot[0])

        original = ffi.cast(
            "void(*)(void *)",
            original_addr
        )

        first_hit = [False]

        @ffi.callback("void(void *)")
        def swap_callback(window):
            # Rendering commands for the frame have already
            # been generated. Restore the real player positions
            # before handing control back to SDL.
            restore_frame_extra_objects()
            restore_frame_shells()

            # Extra-info overlay is rendered after StarBreak's
            # normal frame, immediately before SDL swaps it.

            if not first_hit[0]:
                first_hit[0] = True

                logging.info(
                    "FRAME SWAP CALLBACK ACTIVE window=%s",
                    hex(int(ffi.cast("uintptr_t", window)))
                )

            original(window)

        SWAP_CALLBACK = swap_callback
        SWAP_ORIGINAL = original
        SWAP_SLOT_PTR = slot

        callback_addr = int(
            ffi.cast("uintptr_t", swap_callback)
        )

        logging.info(
            "FRAME SWAP installing slot=%s original=%s callback=%s",
            hex(slot_addr),
            hex(original_addr),
            hex(callback_addr)
        )

        slot[0] = callback_addr

        logging.info("FRAME SWAP INSTALLED")

    except Exception:
        logging.exception("frame swap hook failed")



@ffi.def_extern()
def kickstart():
    global STAGE

    root = os.path.dirname(os.environ["SBPE_SYMFILE"])

    logging.basicConfig(
        filename=os.path.join(root, "shell_hider.log"),
        filemode="w",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s"
    )

    try:
        with open(os.environ["SBPE_SYMFILE"], "r") as f:
            symbols = json.load(f)

        slide = int(os.environ["SBPE_SLIDE"], 0)

        STAGE = ffi.cast(
            "struct Stage **",
            int(symbols["stage"]) + slide
        )

        logging.info(
            "READ-ONLY startup ok; slide=%s stage_addr=%s",
            hex(slide),
            hex(int(symbols["stage"]) + slide)
        )

        threading.Thread(
            target=stage_monitor,
            daemon=True
        ).start()

        threading.Thread(
            target=glclear_slot_probe,
            daemon=True
        ).start()

        threading.Thread(
            target=glclear_passthrough_test,
            daemon=True
        ).start()

        threading.Thread(
            target=swap_passthrough_test,
            daemon=True
        ).start()

    except Exception:
        logging.exception("READ-ONLY startup failed")
        return 1

    return 0
