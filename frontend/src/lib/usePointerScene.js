import { useEffect, useRef, useState } from "react";

/**
 * Pointer state for the hero, in the form the scene actually wants.
 *
 * Returns a normalised position plus two proximity weights — how near the
 * cursor is to the bear side and to the bull side — so consumers can drive
 * brightness and energy directly instead of each recomputing the same geometry
 * from raw coordinates.
 *
 * Three things this deliberately does:
 *
 * 1. **Writes to a ref and to CSS variables, not to React state, on every
 *    move.** Pointer events fire far faster than React can usefully re-render,
 *    and re-rendering a hero full of SVG on each one drops frames on exactly
 *    the hardware that most needs the budget. The variables are read straight
 *    by the compositor. The single piece of state is `active`, which changes
 *    at most twice per visit.
 *
 * 2. **Eases toward the target rather than snapping.** Raw pointer position
 *    makes the parallax feel twitchy and cheap; a low-pass filter on a rAF loop
 *    is the difference between "expensive" and "jittery", and costs one lerp.
 *
 * 3. **Refuses to run when it should not.** `prefers-reduced-motion` and
 *    coarse pointers both skip the loop entirely — on a touch device there is
 *    no hover to react to, and running a rAF loop to animate nothing is a
 *    battery cost with no visual return.
 */

const EASE = 0.075;          // per-frame approach rate; lower is heavier
const REST_EPSILON = 0.0006; // below this, stop the loop rather than idle

export function usePointerScene() {
  const ref = useRef(null);
  const [active, setActive] = useState(false);

  useEffect(() => {
    const element = ref.current;
    if (!element) return undefined;

    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const coarse = window.matchMedia?.("(pointer: coarse)").matches;
    if (reduced || coarse) return undefined;

    // Target is where the pointer is; current is where the scene has eased to.
    const target = { x: 0, y: 0, inside: 0 };
    const current = { x: 0, y: 0, inside: 0 };
    let frame = 0;

    const write = () => {
      const { x, y, inside } = current;

      // Proximity weights. `x` runs -1 (far left) to +1 (far right), so each
      // side's weight is how far the cursor has travelled into that half,
      // clamped at the midline. Squared, so the effect stays subtle until the
      // cursor is genuinely close — a linear ramp lights both animals at once
      // and reads as a global brightness change rather than a reaction.
      const bear = Math.max(0, -x) ** 2 * inside;
      const bull = Math.max(0, x) ** 2 * inside;
      // Peaks at the midline and falls away fast: the collision only exists
      // when the cursor is actually between them.
      const centre = Math.max(0, 1 - Math.abs(x) * 2.2) * inside;

      element.style.setProperty("--px", x.toFixed(4));
      element.style.setProperty("--py", y.toFixed(4));
      element.style.setProperty("--bear-energy", bear.toFixed(4));
      element.style.setProperty("--bull-energy", bull.toFixed(4));
      element.style.setProperty("--centre-energy", centre.toFixed(4));
    };

    const tick = () => {
      let moved = 0;
      for (const key of ["x", "y", "inside"]) {
        const delta = target[key] - current[key];
        current[key] += delta * EASE;
        moved += Math.abs(delta);
      }
      write();

      // Park the loop once the scene has settled. Without this the page holds
      // a rAF callback open for the whole visit doing arithmetic that changes
      // nothing, which is a real cost on a laptop running on battery.
      if (moved > REST_EPSILON) frame = requestAnimationFrame(tick);
      else frame = 0;
    };

    const wake = () => {
      if (!frame) frame = requestAnimationFrame(tick);
    };

    const onMove = (event) => {
      const rect = element.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      // -1..1 on both axes, measured from the centre of the hero.
      target.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      target.y = ((event.clientY - rect.top) / rect.height) * 2 - 1;
      target.inside = 1;
      wake();
    };

    const onLeave = () => {
      // Ease back to neutral rather than cutting, so leaving the hero does not
      // snap the whole composition.
      target.x = 0;
      target.y = 0;
      target.inside = 0;
      setActive(false);
      wake();
    };

    const onEnter = () => setActive(true);

    element.addEventListener("pointermove", onMove, { passive: true });
    element.addEventListener("pointerenter", onEnter, { passive: true });
    element.addEventListener("pointerleave", onLeave, { passive: true });
    write();

    return () => {
      element.removeEventListener("pointermove", onMove);
      element.removeEventListener("pointerenter", onEnter);
      element.removeEventListener("pointerleave", onLeave);
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  return { ref, active };
}
