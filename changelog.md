# Changelog — ROTTR Mod Menu

## [2.7.0] — 2026-10-16

This release reviews the supplied Cheat Engine tables and adds only a small set of features that could be ported using recognizable instruction signatures. A matching signature reduces the risk of writing to an unrelated address, but it does **not** prove that a feature is safe or compatible with every game build.

### Added

- **Maintain 99 Skill Points** — ports the legacy skill-point hook. The feature uses an instruction-signature scan and refuses to enable unless the signature is found exactly once.
- **Infinite Warmth (Endurance)** — adds an Endurance-mode toggle based on the supplied table's warmth instruction. It writes a high warmth value through a signature-based hook.
- **Infinite Hunger (Endurance)** — adds an Endurance-mode toggle based on the supplied table's hunger instruction. It writes a high hunger value through a signature-based hook.
- **Fail-closed matching for new hooks** — the new skill-point and Endurance hooks require a unique signature. If a signature is missing or matches more than once, the trainer should refuse to apply that patch rather than guess.

### Retained

- **Unlimited Ammo (with reload)** — retains the existing multi-site patch and expects the three known weapon-path matches.
- **Unlimited Ammo (no reload needed)** — retains the unique-signature patch for the ammo-decrement instruction.
- **Unlimited Survival Instinct** — retained from the prior release.
- **Unlimited Resources + Ammo** — retained from the prior release.
- **Double Resource Gains (experimental)** — retained as an experimental change that repeats a 16-bit resource addition on one legacy code path. It is **not** a carrying-capacity increase and has not been confirmed in a live game session.

### Not added, and why

- **Unlimited Health / God Mode** — deliberately remains removed. A prior attempt reproducibly crashed the user's game after taking hits. The health-related scripts differ in their assumptions about the player data and hook location; reintroducing one without a reliable build-specific implementation risks another crash.
- **Maximum resource carrying capacity / Deep Pockets** — not implemented. The supplied “Deep Pockets!” script replays an inventory-count addition; a separate `Max Cap` mention appears only as a comment in an inventory-lookup script. Neither establishes a verified writable capacity value for the user's Epic Games build. Doubling resource gains is a separate behavior and must not be presented as capacity.
- **Score Attack score editing / fixed-offset score patch** — not implemented. The supplied score script uses a hard-coded executable offset and a code path that may run repeatedly. There is not enough evidence that writing to that location is safe for this game mode or the user's executable version.
- **Blind fixed-offset resource or skill-point writes** — not copied as-is. Absolute offsets from a Cheat Engine table can refer to different code after an executable update. Signature-based hooks are used where a recognizable pattern was available; they still need live verification.
- **Other raw pointer and inventory entries** — not converted into trainer toggles because the tables do not sufficiently establish stable pointer chains, target types, or safe write behavior for the current game build.

### Compatibility and testing notes

- Target discussed during development: **Rise of the Tomb Raider: 20 Year Celebration, Epic Games**.
- The Cheat Engine tables include scripts and offsets from different dates/build assumptions. A table entry working in Cheat Engine does not guarantee that a port to another executable build will work.
- Source-level checks are not a substitute for a live game test. The v2.7 additions have **not** been verified in a live game session here, and no claim is made that they are crash-proof.
- Test only one newly added option at a time. If the trainer reports that a signature was not uniquely found, leave that option disabled. If gameplay behaves abnormally, disable the option and close the trainer.
- Keep a backup of saves before testing memory-modifying features.

