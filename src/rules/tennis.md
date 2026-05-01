# Tennis line-call rules (working summary)

Plain-language summary of the relevant ITF Rules of Tennis governing line
calls. Use the section that matches the play type and context. For
competition use, consult the full official rulebook.

## General principle (applies to every shot)

A ball is **good (in)** if any part of the ball touches any part of the line.
A ball is **out** only if it lands entirely outside the boundary lines, with
no part of the ball touching any part of the line. The lines are part of
the court they bound.

Camera angle, motion blur, occlusion, or low resolution can make the bounce
indeterminable; in that case the verdict is **inconclusive**.

## Serve

A legal serve must land in the **diagonally opposite service box**, bounded by:
- the **service line** (the back of the box)
- the **center service line** (the inside of the box)
- the **singles sideline** (the outside of the box; same line in singles and doubles)
- the **net** (the front of the box)

A serve that lands outside this box is a **fault**, regardless of whether
the singles or doubles match — the service-box geometry is the same.

Special situations on the serve (we do NOT judge these):
- Foot faults
- Net touches (lets)
- Second-serve vs first-serve sequencing

If the clip shows a serve where the bounce is clearly inside the service
box, the call should be `IN`. If clearly outside, `OUT`. If it grazes any
of the four lines, it is `IN`.

## Groundstroke — singles

After the serve, in **singles**, the ball must stay within:
- the **baseline** (back boundary on each end)
- the **singles sideline** (narrower of the two side boundaries)

The doubles sideline and doubles alley are **not in play** in singles. A
ball landing in the doubles alley is `OUT`.

## Groundstroke — doubles

After the serve, in **doubles**, the ball must stay within:
- the **baseline** (back boundary on each end, same as singles)
- the **doubles sideline** (the wider boundary; the outside line of the alley)

The doubles alley **is in play** in doubles after the serve. A ball that
lands inside the alley is `IN`. The singles sideline is no longer the
boundary.

Note: the *serve* in doubles still uses the singles-sideline-bounded service
box. Only after the serve is returned does the alley come into play.

## Let / net touches

We do not adjudicate lets or net touches. If the clip's call hinges on
whether the ball touched the net, return `inconclusive` with
`headline: "Not a line-call play"`.

## What we explicitly do NOT judge

- Foot faults
- Net touches / lets
- Hindrance, time violations, code violations
- Whether the player reached the ball before the second bounce
- Whether the racket crossed the net plane

If the clip shows one of these instead of a line call, return verdict
`inconclusive` with `headline: "Not a line-call play"`.

## Identifying the play

Before judging, identify these from the clip:
- **play_type**: `serve` or `groundstroke`
- **context**: `singles` or `doubles` (look for one or two players per side)
- the **specific line** the bounce is closest to (baseline, singles sideline,
  doubles sideline, service line, center service line)

Apply the section above that matches. Report the play_type and context in
the JSON output so the user can verify your interpretation.
