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

## License

Licensed under either of [Apache License, Version 2.0](LICENSE-APACHE) or [MIT license](LICENSE-MIT) at your option.
