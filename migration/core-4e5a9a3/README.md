# Hiddify Core 4e5a9a3 rebase checkpoint

The VetrOFF source baseline is commit `642146bcfff89a1dad764cab29118b68bb1d6561`.
This checkpoint records the resolved Core paths for the merge with original
Hiddify Core `4e5a9a357003d0a995360586702fd24c53042e3f`. It includes the new log-stream regression tests.
The target Core still references sing-box `a26e58b376987a3305b201ec87fbc8918f620ad3`.

- `core-resolutions.patch.gz` applies to the exact original Core target.
- `restored-paths.txt` lists the resolved paths to take from that patched target.
- `restored-index.txt` records their verified Git file modes and blob IDs.
- `pending-paths.txt` lists the remaining unmerged Core paths.

To resume, apply the VetrOFF patch series to its pinned Core in a Git checkout
with the original Core history, commit the patched tree, and merge the target
with `--no-commit --no-ff`. In a separate worktree at the exact target,
decompress and apply the checkpoint patch. Copy only the paths listed in
`restored-paths.txt` from that worktree into the merge worktree and stage them.
Keep the remaining merge conflicts for review. The saved patch was verified
against the staged blob IDs. The complete Core and sing-box stack still needs
integration tests and the five-platform build before its pins can move.
