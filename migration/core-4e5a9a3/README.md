# Hiddify Core 4e5a9a3 rebase checkpoint

The VetrOFF source baseline is commit `642146bcfff89a1dad764cab29118b68bb1d6561`,
with the gRPC log-buffer changes from `9091778cc6ba89c8fd5728121c7c86c3a4dfa720`.
The original Core target is `4e5a9a357003d0a995360586702fd24c53042e3f`.
It pins sing-box `a26e58b376987a3305b201ec87fbc8918f620ad3`.

All 45 Core merge conflicts are resolved in the candidate tree. The full binary
patch includes the clean merges as well and can reconstruct the entire staged
Core tree directly from the original target.

Apply `gzip -dc core-resolutions.patch.gz | git apply --index` in a clean checkout
of the exact original Core target. The resulting tree must match
`candidate-tree.txt`; the changed paths and their staged blob IDs are recorded
in `restored-paths.txt` and `restored-index.txt`.

The Core patch is for integration testing. The paired sing-box migration and
the integrated build and tests still need to pass. Keep the published Core
and sing-box pins together until those checks pass.
