# results

Accepted integrator submissions live here, one directory per integrator id:

```
results/<integrator-id>/<pointer>/<YYYYMMDD>.json
```

It is empty because no integrator has published yet. The first accepted
submission creates its own directory.

This tree is **append-only**. The submission gate refuses any pull request that
deletes or renames a file under it, so a published result cannot be withdrawn
through a pull request.

Worked examples of both accepted formats are in [`examples/`](../examples), and
the same documents are used as test fixtures in
[`testdata/results/`](../testdata/results) so the self-test has accepted
submissions to work on without publishing invented ones here.
