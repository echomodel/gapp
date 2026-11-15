# Principles

These are fitness functions for gapp's design commitments: each test
holds one property gapp has on purpose. They are ordinary unit tests and
run with the rest of the suite.

**Do not edit, delete, skip or loosen a test here to make a change
pass.** A failure here means the work in hand breaks a commitment. Stop,
and tell the owner:

1. which commitment (the test's docstring states it, and names its
   section in CONTRIBUTING.md),
2. what in the work breaks it,
3. the options: change the work, or change the commitment.

A test here changes only on the owner's explicit yes, together with the
reason in CONTRIBUTING.md under "Properties kept on purpose".

Adding a test for a new commitment is welcome. It needs the same three
things in its docstring: the commitment, why, and where it is written
down.
