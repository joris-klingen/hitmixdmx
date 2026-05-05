# Building Ableton Live Lighting Clips for DMXIS

A reference for generating `.als` files that drive a DMXIS plugin via clip envelopes. Distilled from extensive trial-and-error; every section here represents a real bug encountered along the way.

## File format basics

An `.als` file is gzipped XML.

```bash
# Decompress
gunzip -kc project.als > project.xml

# Inspect / edit
# ... XML manipulation ...

# Recompress
gzip -c project.xml > project.als
```

Use Python's `xml.etree.ElementTree` for parsing. Write back with `ET.tostring(root, encoding='utf-8', xml_declaration=True)` and gzip the result.

## The DMXIS plugin model

DMXIS appears in the `.als` as a `PluginDevice` with `PluginFloatParameter` children. Each parameter corresponds to one DMX channel.

**Critical:** the file may contain 128 `PluginFloatParameter` elements but only some are actually configured. Identify configured ones by:

```python
configured = p.find('ParameterId').get('Value') != '-1'
```

Unconfigured parameters have `ParameterId='-1'`, `VisualIndex='1073741823'` (= 2³⁰−1), `Manual='0.1234567687'`, and `LastUserRange/First='Invalid'`. Writing envelopes targeting these will crash Live — they exist as stub slots until the user configures them in DMXIS itself. The user has to expose more channels through DMXIS's preset; you cannot add channels by editing the `.als` alone.

In a fully configured file, the parameter list goes 0..N-1 where N is the channel count, named "1".."N", each with a unique `ParameterId`.

## Channel → AutomationTargetId formula

Each `PluginFloatParameter` has a `ParameterValue/AutomationTarget` with an `Id`. Envelopes reference these Ids via `EnvelopeTarget/PointeeId`. For a sequentially configured DMXIS instance, the formula holds:

```python
def at_id(channel):
    """channel is 1-indexed DMX channel number"""
    return 23980 + 2 * (channel - 1)
```

The base value (23980 here) varies by file. To find it: look up the `AutomationTarget` Id of the first `PluginFloatParameter` (the one named "1"). The stride is always 2 because each parameter has both an `AutomationTarget` and a `ModulationTarget` consuming consecutive Ids.

**Verify before using.** Check that `at_id(channel)` matches the actual `AutomationTarget` Id for several channels before generating clips.

## Clip envelope structure

A `MidiClip` lives in `LiveSet/Tracks/MidiTrack/.../ClipSlotList/ClipSlot/ClipSlot/Value/MidiClip`. Inside it, `Envelopes/Envelopes/ClipEnvelope` blocks define automation. Minimum viable envelope:

```xml
<ClipEnvelope Id="<unique>">
  <EnvelopeTarget>
    <PointeeId Value="<at_id(channel)>" />
  </EnvelopeTarget>
  <Automation>
    <Events>
      <FloatEvent Id="0" Time="-63072000" Value="0" />
      <FloatEvent Id="<unique>" Time="-1" Value="0" />
      <!-- your events here, in monotone time order -->
      <FloatEvent Id="<unique>" Time="<clip_len>" Value="<final>" />
    </Events>
    <AutomationTransformViewState>
      <IsTransformPending Value="false" />
      <TimeAndValueTransforms />
    </AutomationTransformViewState>
  </Automation>
</ClipEnvelope>
```

Both the `t=-63072000` sentinel and the `t=-1` event are required. `AutomationTransformViewState` is also required — without it Live may crash.

## Time and value invariants — Live will crash if violated

These were the actual bugs that caused crashes. Bake them into a validator that runs *before* writing the file:

1. **Times must be monotone non-decreasing.** Equal times are fine (used for instant jumps), but `t[i+1] < t[i]` will crash Live when the clip is launched.

2. **No event time may exceed the clip's `LoopEnd`.** Events past the loop end can produce out-of-order sequences when combined with a trailing terminator and crash Live.

3. **Values must be in `[0, 1]`.** Values outside this range can corrupt the envelope state.

4. **All envelope `PointeeId` values must reference configured plugin parameters.** Targeting an unconfigured stub crashes Live.

The fix in code:

```python
# Sort by time
evs_sorted = sorted(events, key=lambda x: x[0])
# Clamp times to clip_len
evs_clamped = [(min(t, clip_len), max(0.0, min(1.0, v))) for t, v in evs_sorted]
# Drop adjacent duplicates (clamping can create them)
evs_final = []
for ev in evs_clamped:
    if not evs_final or evs_final[-1] != ev:
        evs_final.append(ev)
# Add trailing terminator only if last event is before clip_len
last_t, last_v = evs_final[-1]
if last_t < clip_len:
    # append (clip_len, last_v)
```

## Instant value jumps

Live encodes step changes as two events at the same time:

```
(t, old_value)
(t, new_value)
```

The first `Value=0` "before" the jump establishes the off state; the second is the rising edge. The same idiom is used for falling edges. A typical "stab" (briefly on, then off):

```python
def stab(time, duration, value):
    return [(time, 0.0), (time, value), (time + duration, 0.0)]
```

For sustained color holds, just write one event at `t_start` with the value and another at `t_end`. Between two events at the same value, Live holds. Between events at different values, Live linearly interpolates (good for fades).

## ID allocation — the `NextPointeeId` counter

`LiveSet/NextPointeeId` is the next unused integer. Every new XML element that takes an `Id` attribute (MidiClip, ClipEnvelope, FloatEvent, Scene) should consume an Id from this counter and increment it. Update `NextPointeeId` to the final value before saving.

Exception: the `t=-63072000` sentinel `FloatEvent` always uses `Id="0"`. Many of these can coexist because Live's Ids are scoped (not globally unique).

```python
next_id = int(liveset.find('NextPointeeId').get('Value'))
def new_id():
    global next_id
    v = next_id
    next_id += 1
    return v

# ... use new_id() for everything ...

liveset.find('NextPointeeId').set('Value', str(next_id))
```

## Scenes and ClipSlots — they must align

This is non-obvious and breaks projects in subtle ways.

Live's Session view is driven by `LiveSet/Scenes`. Each `Scene` has an `Id`. Each track has a `ClipSlotList` with `ClipSlot` elements, each also with an `Id`. **Live binds clip slots to scenes by position-matched Id**: position 0's ClipSlot Id must equal position 0's Scene Id, position 1's ClipSlot Id must equal position 1's Scene Id, and so on.

**Rules:**

- The number of scenes determines how many session rows render.
- A track may have more `ClipSlot` entries in XML than there are scenes; extras are ignored.
- ClipSlot Ids in extra positions can duplicate Ids from earlier positions — that's fine because Live ignores them.
- If you extend the scene list, you must give the new ClipSlots in *every track* matching new Ids at the corresponding positions.
- Do not arbitrarily rewrite existing slot Ids without rewriting the corresponding scene Ids — and vice versa. Cross-references elsewhere (e.g., `SavedPlayingSlot` is positional, not Id-based, so that's safe, but other things may break).

**Recommended approach:** ask the user to provide a template `.als` with the scene count they need pre-configured, then drop clips into existing slots without touching scene/slot structure. This avoids the entire class of binding bugs.

## Cloning a template clip

Don't construct a `MidiClip` from scratch. Copy an existing one (from the same file or a known-good template), then mutate. The clip carries a lot of view state (`ScrollerTimePreserver`, `TimeSelection`, grid settings, etc.) that's tedious to recreate but harmless to inherit.

```python
import copy
new_clip = copy.deepcopy(template_clip)
new_clip.set('Id', str(new_id()))
new_clip.find('Name').set('Value', name)
new_clip.find('Color').set('Value', str(color_index))  # 0..69
new_clip.find('CurrentStart').set('Value', '0')
new_clip.find('CurrentEnd').set('Value', str(clip_len))
loop = new_clip.find('Loop')
loop.find('LoopStart').set('Value', '0')
loop.find('LoopEnd').set('Value', str(clip_len))
loop.find('OutMarker').set('Value', str(clip_len))
loop.find('HiddenLoopStart').set('Value', '0')
loop.find('HiddenLoopEnd').set('Value', str(clip_len))

# Wipe envelopes
envs_inner = new_clip.find('Envelopes/Envelopes')
for ce in list(envs_inner):
    envs_inner.remove(ce)

# ... build new envelopes ...
```

Clip lengths are in beats. At 122 BPM, 4 beats = ~2 seconds.

## Pixel/fixture mapping

For RGB pixel strips, three consecutive DMX channels per pixel:

```python
def pixel_channels(p):
    """Returns (R, G, B) DMX channel numbers for pixel p (1-indexed)."""
    base = 1 + (p - 1) * 3
    return base, base + 1, base + 2
```

Multi-fixture setups need a per-fixture base address. Always confirm physical orientation with the user — pixel 1 may be at the top or bottom of the fixture, and bars may be wired left-to-right or right-to-left. Get this wrong and animations look upside-down or mirrored.

## Patterns I've validated

These produce visually distinct shows and all pass the validator:

- **Stabs** — `stab(time, duration, value)` building block. Works for kicks, claps, strobes.
- **Holds** — two events at the same value separated in time. For static color blocks.
- **Fades / sweeps** — keyframes at intermediate values; Live interpolates linearly.
- **HSV-driven color** — convert to RGB at each keyframe time. For rainbow scrolls, aurora, fire, etc.
- **Sample-and-hold randomization** — use a seeded `random.Random` so the clip is deterministic. Lightning, glitch, fireworks all use this.

For smooth animations (rainbows, breathing, comets), 4–16 keyframes per beat is enough. More is wasted; fewer looks steppy.

## Color helpers

```python
def hsv_to_rgb(h, s, v):
    h = h % 1.0
    i = int(h * 6); f = h * 6 - i
    p = v * (1 - s); q = v * (1 - f * s); t = v * (1 - (1 - f) * s)
    i = i % 6
    if i == 0: return v, t, p
    if i == 1: return q, v, p
    if i == 2: return p, v, t
    if i == 3: return p, q, v
    if i == 4: return t, p, v
    return v, p, q
```

Clip color attribute (the colored stripe in Live's UI) is an integer 0..69 indexing Live's palette. Useful values: 1=red, 2=green, 4=blue, 5=blue-light, 9=purple, 12=amber, 14=orange, 15=yellow, 16=red-bright.

## Validator (run before saving)

```python
def validate(root):
    ls = root.find('LiveSet')
    scenes = list(ls.find('Scenes'))
    issues = []
    for track in ls.find('Tracks'):
        if track.tag not in ('AudioTrack', 'MidiTrack'): continue
        slots = track.findall('.//ClipSlotList/ClipSlot')
        for i in range(min(len(scenes), len(slots))):
            if slots[i].get('Id') != scenes[i].get('Id'):
                issues.append(f"track {track.tag}: pos {i} slot/scene Id mismatch")
        if track.tag != 'MidiTrack': continue
        plugin = track.find('.//PluginDevice')
        if plugin is None: continue
        configured = {p.find('ParameterValue/AutomationTarget').get('Id')
                      for p in plugin.findall('.//PluginFloatParameter')
                      if p.find('ParameterId').get('Value') != '-1'}
        for i, s in enumerate(slots):
            c = s.find('ClipSlot/Value/MidiClip')
            if c is None: continue
            clip_end = float(c.find('Loop/LoopEnd').get('Value'))
            for env in c.findall('.//Envelopes/Envelopes/ClipEnvelope'):
                pid = env.find('EnvelopeTarget/PointeeId').get('Value')
                if pid not in configured:
                    issues.append(f"slot {i}: envelope targets unconfigured param {pid}")
                evs = env.findall('Automation/Events/FloatEvent')
                ts = [float(e.get('Time')) for e in evs]
                for j in range(1, len(ts)):
                    if ts[j] < ts[j-1]:
                        issues.append(f"slot {i}: non-monotone time {ts[j-1]} -> {ts[j]}")
                        break
                for t in ts:
                    if t > clip_end + 1e-9 and t > 0:
                        issues.append(f"slot {i}: event past clip end (t={t}, end={clip_end})")
                        break
                for e in evs:
                    v = float(e.get('Value'))
                    if not (0 <= v <= 1):
                        issues.append(f"slot {i}: value {v} out of range")
                        break
    return issues
```

Always run this before writing the file. If any issues, fix and re-run; never ship a file that fails validation.

## Common workflow

1. Receive a template `.als` from the user. Decompress.
2. Inspect: count scenes, count configured plugin params, find the channel→at_id mapping, locate a populated clip to clone.
3. Build patterns as `dict[channel_number, list[(time, value)]]`. Sort, clamp, dedupe.
4. Clone the template clip, set name/color/length, replace envelopes.
5. Insert into a target slot's `<Value>` element (deleting any existing clip first).
6. Update `NextPointeeId`.
7. Run the validator. Fix any issues.
8. Re-serialize, gzip, save with `.als` extension.

## Things that are not in scope from `.als` editing alone

- Configuring DMXIS channels (must be done in the plugin)
- Adding/removing tracks (possible but invasive; the user's template should already have what's needed)
- Adding/removing scenes (possible but error-prone — see Scenes section)
- Editing clip color via custom RGB (the integer palette is the only supported path)
