# msstore-submit

Shared GitHub Actions workflow for working with Microsoft Store submissions of [akivajp](https://github.com/akivajp)'s apps (FlexFlyout, rigtop, …), through the Microsoft Store submission API.

It signs in **without a client secret**: the job's GitHub OIDC token is exchanged for an Entra ID token of the app registered in Partner Center, which trusts each calling repository through a federated credential. Nothing expires, and nothing secret is stored in the repositories.

## Using it from an app's repository

1. Create the environment `msstore` in the repository (Settings → Environments, or `gh api -X PUT repos/<owner>/<repo>/environments/msstore`).
2. On the Entra ID app (Azure portal → Microsoft Entra ID → App registrations → the app → Certificates & secrets → Federated credentials → Add credential), add a credential with the scenario "GitHub Actions deploying Azure resources", entity type **Environment**, environment `msstore`, and the repository's owner, owner ID, name and repository ID (`gh api repos/<owner>/<repo> --jq '{owner_id: .owner.id, repo_id: .id}'`). The subject is `repo:<owner>@<owner_id>/<repo>@<repo_id>:environment:msstore`.
3. Call the workflow:

   ```yaml
   jobs:
     store:
       permissions:
         contents: read
         id-token: write
       uses: akivajp/msstore-submit/.github/workflows/store.yml@main
       with:
         app-id: 9P6KDQX7JZS4
         command: check
   ```

## Commands

- `check`: reads the app and its submissions and shows their state; changes nothing.
- `show`: prints the pending (or the last published) submission as JSON, without its upload URL; changes nothing.
- `submit`: makes a new submission from the last published one, replaces the package (`artifact` and `package`; with one package per architecture, a `package` pattern that matches them all and their number as `package-count`), applies the listing files (`listings`: a folder of `<language>.toml` files; see `msstore.py --help` for the fields) and, with `images`, their screenshots; then commits it (`commit: true`) and follows it until certification starts, or leaves it as a draft to look at in Partner Center.
- `commit`: commits the pending submission (a draft).

**Partner Center shows a submission made through the API only as it was before, until it is committed**, and a submission edited or submitted in Partner Center can no longer be changed or committed through the API. Look at a draft's changes in the workflow's log (`submit` prints them) or with `show`, and commit it with `commit`, not in Partner Center.
- `discard`: deletes the pending submission.

## License

Licensed under either of [Apache License, Version 2.0](LICENSE-APACHE) or [MIT license](LICENSE-MIT) at your option.
