"""Build a reversible SnowRunner Functions-menu supplies transfer patch.

Operates on an extracted, cargo-patched initial.pak. Never edits saves.
The bundle decoder preserves protobuf fields it does not modify.
"""
import copy
import functools
import json
import struct
import sys
import zlib
from pathlib import Path

import _protobuf as inspection


def dec(b):
    return [(f, w, v) for f, w, v, *_ in inspection.decode(b)]


def vint(n):
    assert n >= 0
    out = bytearray()
    while n >= 128:
        out.append((n & 127) | 128)
        n >>= 7
    out.append(n)
    return bytes(out)


def enc(fields):
    out = bytearray()
    for f, w, v in fields:
        out += vint((f << 3) | w)
        if w == 0:
            out += vint(v)
        elif w == 2:
            out += vint(len(v)) + v
        elif w in (1, 5):
            out += v
        else:
            raise ValueError(w)
    return bytes(out)


def one(fields, field):
    values = [v for f, w, v in fields if f == field]
    assert len(values) == 1, (field, len(values))
    return values[0]


def replace(fields, field, value, wire=None):
    assert sum(f == field for f, w, v in fields) == 1
    return [(f, (wire if wire is not None else w), value) if f == field else (f, w, v)
            for f, w, v in fields]


def instructions(fields):
    return [((v - 0x80000000) & 255, (v - 0x80000000) >> 8)
            for f, w, v in dec(one(fields, 5))]


def code_bytes(code):
    return enc([(1, 0, 0x80000000 + (arg << 8) + op) for op, arg in code])


BRANCHES = (22, 24, 25, 26)


def insert_code(code, before, removed=()):
    """Keep old branch targets at the beginning of inserted blocks.

    Each inserted instruction can target an old instruction with ('old', index)
    or a local label string. Labels are (None, label) entries.
    """
    oldmap, labels, result = {}, {}, []
    for i in range(len(code) + 1):
        oldmap[i] = len(result)
        for op, arg in before.get(i, []):
            if op is None:
                assert arg not in labels
                labels[arg] = len(result)
            else:
                result.append((op, arg))
        if i < len(code) and i not in removed:
            op, arg = code[i]
            result.append((op, ('old', arg) if op in BRANCHES else arg))
    out = []
    for op, arg in result:
        if isinstance(arg, tuple):
            arg = oldmap[arg[1]]
        elif isinstance(arg, str):
            arg = labels[arg]
        out.append((op, arg))
    assert all(0 <= arg <= len(out) for op, arg in out if op in BRANCHES)
    return out


class Module:
    def __init__(self, name, data):
        self.name = name
        self.header = data[:16]
        assert self.header[:4] == b'SSLB'
        self.fields = dec(data[16:])
        assert enc(self.fields) == data[16:], 'noncanonical protobuf'

    @property
    def strings(self):
        return [v.decode() for f, w, v in self.fields if f == 6]

    def string(self, text):
        if text in self.strings:
            return self.strings.index(text)
        i = len(self.strings)
        self.fields.append((6, 2, text.encode()))
        return i

    def add(self, field, value):
        index = sum(f == field for f, w, v in self.fields)
        self.fields.append((field, 2, enc(value)))
        return index

    def symbols(self):
        return [dec(v) for f, w, v in self.fields if f == 5]

    @functools.lru_cache(maxsize=None)
    def builtin(self, name):
        refs = dec(one(self.fields, 4))
        ti = [one(dec(v), 1) for f, w, v in refs if f == 1].index(self.string(name))
        return next(i for i, ss in enumerate(self.symbols())
                    if one(ss, 1) == 3 and one(ss, 2) == ti)

    def mutate_class(self, name, mutate):
        updated = []
        found = False
        for f, w, v in self.fields:
            if f == 7 and self.strings[one(dec(v), 1)] == name:
                v = enc(mutate(dec(v)))
                found = True
            updated.append((f, w, v))
        assert found, name
        self.fields = updated

    def function_index(self, name):
        found = []
        for f, w, v in self.fields:
            if f == 7:
                for k, ww, vv in dec(v):
                    if k == 3:
                        row = dec(vv)
                        if self.strings[one(row, 1)] == name and one(row, 3) == 2:
                            found.append(one(row, 4))
        assert len(found) == 1, (self.name, name, found)
        return found[0]

    def get_function(self, name):
        return dec([v for f, w, v in self.fields if f == 8][self.function_index(name)])

    def set_function(self, name, fields):
        index = self.function_index(name)
        count = 0
        out = []
        for f, w, v in self.fields:
            if f == 8:
                if count == index:
                    v = enc(fields)
                count += 1
            out.append((f, w, v))
        self.fields = out

    def patch_code(self, name, patch):
        fields = self.get_function(name)
        self.set_function(name, replace(fields, 5, code_bytes(patch(instructions(fields)))))

    def constant(self, fields, const):
        index = sum(f == 4 for f, w, v in fields)
        # Constants precede code in the original format.
        at = next(i for i, (f, w, v) in enumerate(fields) if f == 5)
        fields.insert(at, (4, 2, enc(const)))
        return index

    def bytes(self):
        payload = enc(self.fields)
        return self.header[:12] + struct.pack('<I', len(payload)) + payload


BASE = 'ui.components.popups.repair.'
HUD = 'ui.hud.hud_controllers.hud_interaction_panel_controller'
ORIGINALS = ['refuel_controller', 'popup_refuel_ui_view_new', 'popup_refuel_ui_controller_new']


def renamed(text, resource):
    suffix = resource.capitalize()
    for old, new in [
        ('PopupRefuelUiControllerNew', 'PopupTransfer' + suffix + 'UiController'),
        ('PopupRefuelUiViewData', 'PopupTransfer' + suffix + 'UiViewData'),
        ('PopupRefuelUiViewNew', 'PopupTransfer' + suffix + 'UiView'),
        ('RefuelController', 'Transfer' + suffix + 'Controller'),
        ('RefueledAddon', 'Transferred' + suffix + 'Addon'),
        ('RefuelIconType', 'Transfer' + suffix + 'IconType'),
        ('popup_refuel_ui_controller_new', 'popup_transfer_' + resource + '_ui_controller'),
        ('popup_refuel_ui_view_new', 'popup_transfer_' + resource + '_ui_view'),
        ('refuel_controller', 'transfer_' + resource + '_controller'),
    ]:
        text = text.replace(old, new)
    return text


def transfer_code(module, kind):
    # VM constants contain type-symbol indices, including cast targets.
    fields = module.get_function('Refuel')
    ci = module.constant(fields, [(1, 0, 5), (6, 0, module.builtin('int'))])
    cf = module.constant(fields, [(1, 0, 5), (6, 0, module.builtin('float'))])
    get, cap, setter = (20, 23, 19) if kind == 'parts' else (22, 24, 21)
    code = []
    emit = lambda op, arg=0: code.append((op, arg))
    label = lambda name: code.append((None, name))
    def load(i): emit(7, i)
    def store(i): emit(59, i); emit(2)
    def call(receiver, method, arguments=()):
        emit(1, 1)
        for arg in arguments: load(arg)
        load(receiver); emit(4, len(arguments)); emit(13, method)
    def require(op, arg): emit(op, arg); emit(22, 'end')
    emit(1, 9)
    emit(1, 1); emit(8); emit(4, 0); emit(13, 99); store(-9)
    load(-9); emit(3); require(48, 8)
    call(-9, 178); emit(27); emit(22, 'end')
    # Resolve selected native add-ons once, then validate distinct inventories.
    for field, local in [(82, -1), (83, -2)]:
        load(0); emit(10, field); emit(3); require(48, 8)
        load(0); emit(10, field); emit(10, 6); store(local)
        load(local); emit(3); require(48, 8)
    load(-1); load(-2); require(48, 8)
    # Ignore nonpositive input. Cast a positive fractional step to one unit.
    load(1); emit(4, 0); emit(53, cf); require(50, 10)
    load(1); emit(53, ci); store(-3)
    load(-3); emit(4, 0); emit(48, 7); emit(22, 'amount_ready')
    emit(4, 1); store(-3)
    label('amount_ready')
    call(-1, get); store(-4)
    call(-2, get); store(-5)
    call(-2, cap); store(-6)
    # Validate stock and remaining capacity before any mutation.
    for loc in (-4, -5):
        load(loc); emit(4, 0); require(49, 12)
    load(-6); load(-5); require(49, 10)
    load(-6); load(-5); emit(36); store(-7)
    for maximum in (-4, -7):
        load(-3); load(maximum); emit(49, 10); emit(22, 'clamped' + str(maximum))
        load(maximum); store(-3)
        label('clamped' + str(maximum))
    load(-3); emit(4, 0); require(49, 10)
    # Mutate through native setters so the normal resource notifications run.
    load(-4); load(-3); emit(36); store(-8)
    call(-1, setter, (-8,)); emit(2)
    load(-5); load(-3); emit(32); store(-8)
    call(-2, setter, (-8,)); emit(2)
    # Read back both values. Roll back both inventories on a mismatch.
    call(-1, get); load(-4); load(-3); emit(36); emit(48, 7); emit(22, 'rollback')
    call(-2, get); load(-5); load(-3); emit(32); emit(48, 7); emit(22, 'rollback')
    emit(26, 'refresh')
    label('rollback')
    call(-1, setter, (-4,)); emit(2)
    call(-2, setter, (-5,)); emit(2)
    label('refresh')
    call(0, 116); emit(2)  # UpdateViewIfNeeded
    label('end'); emit(19, 2)
    result = insert_code([], {0: code})
    module.set_function('Refuel', replace(fields, 5, code_bytes(result)))
    # Add native member references with accurate int signatures for the linker.
    refs = dec(one(module.fields, 4))
    addon_type = next(one(dec(v), 3) for f, w, v in refs
                      if f == 4 and module.strings[one(dec(v), 2)] == 'GetFuel')
    sig_get = module.add(5, [(1, 0, 9), (4, 2, enc([(1, 0, module.builtin('int'))]))])
    sig_set = module.add(5, [(1, 0, 9), (4, 2, enc([
        (1, 0, module.builtin('void')), (2, 0, module.builtin('int'))]))])
    methods = [('GetRepairs', 20), ('GetRepairsCapacity', 23), ('SetRepairs', 19)] if kind == 'parts' else [
        ('GetWheelRepairs', 22), ('GetWheelRepairsCapacity', 24), ('SetWheelRepairs', 21)]
    for name, field in methods:
        refs.append((4, 2, enc([(1, 0, 1), (2, 0, module.string(name)),
                              (3, 0, addon_type), (4, 0, field),
                              (5, 0, sig_set if name.startswith('Set') else sig_get)])))
    session_type = next(one(dec(v), 3) for f, w, v in refs
                        if f == 4 and module.strings[one(dec(v), 2)] == 'GetFuelController')
    sig_bool = module.add(5, [(1, 0, 9), (4, 2, enc([(1, 0, module.builtin('bool'))]))])
    refs.append((4, 2, enc([(1, 0, 2), (2, 0, module.string('IsMultiplayer')),
                          (3, 0, session_type), (4, 0, 178), (5, 0, sig_bool)])))
    module.fields = replace(module.fields, 4, enc(refs))
    module.fields.append((2, 0, module.string('game.game_modes.game_session')))
    for name in ('IsHasRefuelObjects', 'IsCanBeShown'):
        guard = [(1, 1), (1, 1), (8, 0), (4, 0), (13, 99), (4, 0), (13, 178),
                 (22, 'singleplayer'), (5, 0), (18, 1), (None, 'singleplayer')]
        module.patch_code(name, lambda c: insert_code(c, {0: guard}))


def patch_popup(module, kind):
    get, cap = (20, 23) if kind == 'parts' else (22, 24)
    # Fuel-less maintenance trailers still have native repair inventories.
    # Preserve the ordinary nearby/null/current-truck checks, but discard the
    # inherited Truck.GetFuelCapacity eligibility check.
    def nearby(code):
        assert code[59] == (13, 412) and code[62] == (22, 70)
        return insert_code(code, {}, set(range(56, 70)))
    module.patch_code('GetNearbyTrucksAndTrailers', nearby)

    def inventory_guard(local, skip):
        return [(7, local), (10, 6), (3, 0), (48, 8), (22, ('old', skip)),
                (1, 1), (7, local), (10, 6), (4, 0), (13, cap),
                (4, 0), (49, 10), (22, ('old', skip))]

    # A truck's fuel tank is not parts/wheel storage. Keep the root inventory
    # only when it has capacity for this resource (including trailer roots).
    def selectors(code):
        assert code[170] == (7, -9) and code[353] == (1, 1)
        empty = []
        for field in (78, 79):
            empty += [(7, 0), (10, field), (65, 0), (4, 0), (49, 10),
                      (22, 'empty_stores')]
        empty += [(26, 'stores_ready'), (None, 'empty_stores')]
        for field in (82, 83):
            empty += [(3, 0), (7, 0), (60, field), (2, 0)]
        empty += [(19, 1), (None, 'stores_ready')]
        return insert_code(code, {170: inventory_guard(-8, 187),
                                  353: inventory_guard(-4, 367), 367: empty})
    module.patch_code('UpdateNearTrucks', selectors)

    # Both popup builders must also tolerate empty inventories while open.
    def empty_guard(end):
        out = []
        for field in (78, 79):
            out += [(7, 0), (10, field), (65, 0), (4, 0), (49, 10),
                    (22, 'no_stores')]
        return out + [(26, 'has_stores'), (None, 'no_stores')] + end + [(None, 'has_stores')]
    module.patch_code('CreateData', lambda c: insert_code(c, {17: empty_guard(
        [(7, 0), (10, 109), (18, 1)])}))
    module.patch_code('CreateMarkers', lambda c: insert_code(c, {25: empty_guard(c[332:340])}))

    # MarkerOnTrucksGarnish already supplies the exact stock repair icons.
    for name in ('CreateMarkers', 'GetRefuelTruckMarker'):
        fields = module.get_function(name)
        out = []
        for f, w, v in fields:
            if f == 4:
                row = dec(v)
                if one(row, 1) == 4 and module.strings[one(row, 5)] == 'Fuel':
                    v = enc(replace(row, 5, module.string('Health' if kind == 'parts' else 'WheelParts')))
            out.append((f, w, v))
        module.set_function(name, out)

    locations = {
        'CreateData': {63: 17, 71: 18, 102: 17, 110: 18},
        'RefuelAll': {23: 18, 27: 17, 36: 17},
        'GetCurrentFuel': {4: 17}, 'GetMaxFuel': {4: 18}}
    # These builders have gained guards, so locate their original native
    # addon getters by opcode instead of relying on shifted instruction IDs.
    for name in ['UpdateNearTrucks'] + list(locations):
        fields = module.get_function(name)
        cast = module.constant(fields, [(1, 0, 5), (6, 0, module.builtin('float'))])
        code = instructions(fields)
        insertions = {}
        places = {i: a for i, (op, a) in enumerate(code)
                  if op == 13 and a in (17, 18) and (name != 'UpdateNearTrucks' or a == 18 and i > 100)}
        # GetGameEngine also uses field 18 on globals and is not an addon.
        if name == 'CreateData':
            assert len(places) == 4
        elif name == 'UpdateNearTrucks':
            assert len(places) == 2
        else:
            assert len(places) == len(locations[name])
        for i, original in places.items():
            assert code[i] == (13, original), (module.name, name, i, code[i])
            code[i] = (13, get if original == 17 else cap)
            insertions[i + 1] = [(53, cast)]
        module.set_function(name, replace(fields, 5, code_bytes(insert_code(code, insertions))))
    transfer_code(module, kind)


def clone_block(code, start, end, prefix, popup, action=None, const=None, method=None):
    result = []
    for i in range(start, end):
        op, arg = code[i]
        if op == 10 and arg == 84: arg = popup
        if action is not None and op == 4 and arg == 13: arg = action
        if const is not None and op == 6 and arg == 17: arg = const
        if method is not None and op == 13 and arg == 139: arg = method
        if op in BRANCHES:
            arg = prefix + str(arg) if start <= arg <= end else ('old', arg)
        result.append((None, prefix + str(i)))
        result.append((op, arg))
    result.append((None, prefix + str(end)))
    return result


def patch_hud(module):
    insertions = {}

    def record_insertions(name, before):
        insertions[name] = [{'at': at, 'length': sum(op is not None for op, _ in rows)}
                            for at, rows in sorted(before.items())]

    parts_type = 'PopupTransferPartsUiController'
    wheels_type = 'PopupTransferWheelsUiController'
    refs = dec(one(module.fields, 4))
    field_rows = []
    for resource, typename, slot in [('parts', parts_type, 169), ('wheels', wheels_type, 170)]:
        module.fields.append((2, 0, module.string(BASE + 'popup_transfer_' + resource + '_ui_controller')))
        ri = sum(f == 1 for f, w, v in refs)
        refs.append((1, 2, enc([(1, 0, module.string(typename))])))
        si = module.add(5, [(1, 0, 4), (9, 2, enc([(1, 0, ri)]))])
        original = None
        for f, w, v in module.fields:
            if f == 7 and module.strings[one(dec(v), 1)] == 'HudInteractionPanelController':
                original = next(dec(vv) for k, ww, vv in dec(v) if k == 3 and
                                module.strings[one(dec(vv), 1)] == 'refuelPopup')
        row = replace(replace(original, 1, module.string(resource + 'TransferPopup')), 2, slot)
        row = replace(row, 8, enc(replace(dec(one(row, 8)), 1, si)))
        field_rows.append((3, 2, enc(row)))
    module.fields = replace(module.fields, 4, enc(refs))
    # Add open methods and serialized popup fields to the existing controller.
    original_fn = module.get_function('OpenRefuelPopup')
    for resource, slot, method in [('parts', 169, 171), ('wheels', 170, 172)]:
        fields = copy.deepcopy(original_fn)
        # Function field 1 is its function-type symbol index, not an ID counter.
        # Give each new method a cloned void() type, preserving its signature.
        fid = module.add(5, copy.deepcopy(module.symbols()[one(original_fn, 1)]))
        fields = replace(fields, 1, fid)
        name_index = module.string('OpenTransfer' + resource.capitalize() + 'Popup')
        fields = replace(fields, 2, enc(replace(dec(one(fields, 2)), 1, name_index)))
        code = [(op, slot if op == 10 and arg == 84 else arg) for op, arg in instructions(fields)]
        fields = replace(fields, 5, code_bytes(code))
        index = module.add(8, fields)
        field_rows.append((3, 2, enc([(1, 0, name_index), (2, 0, method),
                                     (3, 0, 2), (4, 0, index), (7, 2, b'')])))
    module.mutate_class('HudInteractionPanelController', lambda c: c + field_rows)
    enum_fields = []
    for name, slot, value in [('TRANSFER_PARTS', 40, 35), ('TRANSFER_WHEELS', 41, 36)]:
        enum_fields.append((3, 2, enc([(1, 0, module.string(name)), (2, 0, slot),
                                      (3, 0, 6), (5, 2, enc([(1, 0, 1), (2, 0, value)])),
                                      (7, 2, b'')])))
    module.mutate_class('TruckActionId', lambda c: c + enum_fields)
    for name, ranges in [('InitContainer', [(184, 231)]),
                         ('Terminate', [(39, 45), (138, 146), (210, 216)])]:
        code = instructions(module.get_function(name))
        before = {}
        for start, end in ranges:
            assert any(op == 10 and arg == 84 for op, arg in code[start:end])
            before[end] = sum([clone_block(code, start, end, name + str(start) + r, slot)
                               for r, slot in [('p', 169), ('w', 170)]], [])
        record_insertions(name, before)
        module.patch_code(name, lambda c: insert_code(c, before))
    fields = module.get_function('CreateTruckData')
    constants = [module.constant(fields, [(1, 0, 4), (5, 0, module.string(key))]) for key in
                 ['UI_TRANSFER_PARTS_ACTION', 'UI_TRANSFER_WHEELS_ACTION']]
    code = instructions(fields)
    assert code[382] == (10, 84) and code[392] == (4, 13)
    before = {401: sum([clone_block(code, 380, 401, 'row' + r, slot, action, const)
                        for r, slot, action, const in [('p', 169, 35, constants[0]),
                                                     ('w', 170, 36, constants[1])]], [])}
    record_insertions('CreateTruckData', before)
    module.set_function('CreateTruckData', replace(fields, 5, code_bytes(insert_code(code, before))))
    fields = module.get_function('HandleActionRequest')
    code = instructions(fields)
    calls = [i for i, (op, arg) in enumerate(code) if (op, arg) == (13, 139)]
    assert len(calls) == 1
    start, end = calls[0] - 13, calls[0] + 4
    assert code[start + 1] == (4, 13)
    before = {end: sum([clone_block(code, start, end, 'action' + r, slot, action, method=method)
                        for r, slot, action, method in [('p', 169, 35, 171), ('w', 170, 36, 172)]], [])}
    record_insertions('HandleActionRequest', before)
    module.set_function('HandleActionRequest', replace(fields, 5, code_bytes(insert_code(code, before))))
    return [{'class': 'HudInteractionPanelController', 'name': name, 'insertions': rows}
            for name, rows in insertions.items()]


def read_bundle(path):
    data = path.read_bytes()
    count = struct.unpack_from('<I', data, 11)[0]
    pos = 20
    names = []
    for _ in range(count):
        size = struct.unpack_from('<I', data, pos)[0]; pos += 4
        names.append(data[pos:pos + size].decode()); pos += size
    assert data[pos] == 1
    sizes = struct.unpack_from('<' + 'I' * count, data, pos + 1)
    pos += 1 + count * 4
    modules = []
    for name, size in zip(names, sizes):
        modules.append((name, data[pos:pos + size])); pos += size
    assert pos == len(data)
    return data[:20], modules


def patch_bundle(path, destination):
    header, originals = read_bundle(path)
    byname = dict(originals)
    assert not any('popup_transfer_parts' in n for n in byname), 'already patched'
    hud = Module(HUD, byname[HUD]); hud_insertions = patch_hud(hud)
    changed = {HUD: hud.bytes()}
    additions = []
    for resource in ('parts', 'wheels'):
        for stem in ORIGINALS:
            name = BASE + stem
            module = Module(name, byname[name])
            module.fields = [(f, w, renamed(v.decode(), resource).encode() if f == 6 else v)
                             for f, w, v in module.fields]
            newname = renamed(name, resource)
            module.name = newname
            # New modules require distinct cache identities. These IDs are not
            # payload checksums: release and debug originals share their IDs.
            module.header = module.header[:8] + struct.pack('<I', zlib.crc32(newname.encode())) + module.header[12:]
            if stem == 'popup_refuel_ui_controller_new':
                patch_popup(module, resource)
            elif stem == 'popup_refuel_ui_view_new':
                fields = module.get_function('GetFuelWithMetrics')
                ci = module.constant(fields, [(1, 0, 5), (6, 0, module.builtin('int'))])
                cf = module.constant(fields, [(1, 0, 5), (6, 0, module.builtin('float'))])
                # Refuel's suggested step is capacity/4. Small wheel stores
                # therefore suggest fractions that truncate to zero. Preserve
                # whole stock counts and make a positive step at least one.
                count_code = [(1, 1), (7, 1), (53, ci), (59, -1), (2, 0),
                              (7, -1), (4, 0), (48, 7), (22, 'count'),
                              (7, 1), (4, 0), (53, cf), (50, 10), (22, 'count'),
                              (4, 1), (18, 2), (None, 'count'), (7, -1), (18, 2)]
                module.set_function('GetFuelWithMetrics', replace(fields, 5,
                    code_bytes(insert_code([], {0: count_code}))))
                fields = module.get_function('UpdateData')
                code = instructions(fields)
                removed = set()
                conversions = [i for i, instr in enumerate(code)
                               if instr == (13, 22) and code[i-2] == (10, 79)]
                assert len(conversions) == 6
                for pos in conversions:
                    code[pos] = (13, 80)
                    removed.add(pos-2)
                units = module.constant(fields, [(1, 0, 4),
                    (5, 0, module.string('UI_TRANSFER_' + resource.upper() + '_UNIT'))])
                unit_calls = [i for i, instr in enumerate(code)
                              if instr == (13, 27) and code[i-2] == (10, 79)]
                assert len(unit_calls) == 1
                unit_start, unit_end = unit_calls[0]-5, unit_calls[0]+4
                assert code[unit_start:unit_calls[0]] == [(1, 1), (1, 1), (7, 0), (10, 79), (4, 0)]
                assert code[unit_end-3][0] == 8 and code[unit_end-2:unit_end] == [(4, 1), (13, 788)]
                removed.update(range(unit_start, unit_end))
                # Keep the original GetLocalizationString call around our unit key.
                localized_unit = [(1, 1), (6, units)] + code[unit_end-3:unit_end]
                module.set_function('UpdateData', replace(fields, 5,
                    code_bytes(insert_code(code, {unit_start: localized_unit}, removed))))
                captions = {
                    'UI_HUD_REFUEL_DIALOG_HEADER': 'UI_TRANSFER_' + resource.upper() + '_HEADER',
                    'UI_HUD_REFUEL_DIALOG_PROMPT_EX': 'UI_TRANSFER_PROMPT',
                    'UI_HUD_REFUEL_DIALOG_PROMPT_EX, ': 'UI_TRANSFER_PROMPT, ',
                    'UI_HUD_REFUEL_DIALOG_PROMPT_EX, 0,': 'UI_TRANSFER_PROMPT, 0,',
                    'UI_HUD_REFUEL_DIALOG_HINT_FULL': 'UI_TRANSFER_FULL',
                    'UI_HUD_REFUEL_DIALOG_HINT_NO_FUEL': 'UI_TRANSFER_NO_STOCK',
                    'UI_HUD_REFUEL_HINT_NO_TARGETS': 'UI_TRANSFER_NO_TARGETS',
                    'UI_HUD_REFUEL_NO_TARGETS': 'UI_TRANSFER_NO_TARGETS',
                    'UI_HUD_REFUEL_FULL_REFUEL_HOLD_HINT': 'UI_TRANSFER_ALL_HINT',
                    '|reFuelValueImg20| %s / %d': ('|rePairValueImg20| %s / %d' if resource == 'parts'
                                                           else '|repairWheelsImg20| %s / %d'),
                }
                module.fields = [(f, w, captions.get(v.decode(), v.decode()).encode() if f == 6 else v)
                                 for f, w, v in module.fields]
            else:
                for fn in ('SubscribesTrucks', 'UnsubscribesTrucks'):
                    fields = module.get_function(fn)
                    out = []
                    for f, w, v in fields:
                        if f == 4 and dec(v) == [(1, 0, 1), (2, 0, 4)]:
                            v = enc([(1, 0, 1), (2, 0, 5)])
                        out.append((f, w, v))
                    module.set_function(fn, out)
                # Replace event refs, including their inherited backing field.
                refs = []
                for f, w, v in dec(one(module.fields, 4)):
                    if f == 4:
                        row = dec(v)
                        if module.strings[one(row, 2)] == 'OnFuelAmountChanged':
                            row = replace(replace(row, 2, module.string('OnRepairsAmountChanged')), 4, 5)
                            v = enc(row)
                    refs.append((f, w, v))
                module.fields = replace(module.fields, 4, enc(refs))
            additions.append((newname, module.bytes()))
    allmodules = [(n, changed.get(n, b)) for n, b in originals] + additions
    assert len({b[8:12] for n, b in allmodules}) == len(allmodules)
    out = bytearray(header)
    struct.pack_into('<I', out, 11, len(allmodules))
    for name, body in allmodules:
        b = name.encode(); out += struct.pack('<I', len(b)) + b
    out.append(1)
    for name, body in allmodules: out += struct.pack('<I', len(body))
    for name, body in allmodules: out += body
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(out)
    checkheader, checked = read_bundle(destination)
    assert checked == allmodules
    for n, b in checked:
        m = Module(n, b)
        symbols = m.symbols()
        for f, w, v in m.fields:
            if f == 8:
                ff = dec(v)
                assert one(symbols[one(ff, 1)], 1) == 9, (n, 'function signature')
                if any(k == 5 for k, ww, vv in ff):
                    code = instructions(ff)
                    assert all(0 <= a <= len(code) for op, a in code if op in BRANCHES), n
    print(path.name, len(originals), '->', len(checked), 'modules;', len(out), 'bytes')
    return hud_insertions


