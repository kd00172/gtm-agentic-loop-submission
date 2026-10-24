# Submission repository instructions

This repository contains a reusable GTM loop, prompts, schemas, and synthetic examples only.

- Never copy company records, customer records, source transcripts, contact lists, screenshots of real accounts, credentials, or private infrastructure details here.
- Keep runtime inputs, model outputs, evidence, exact messages, approvals, and logs in a separate directory outside this repository. Do not use symlinks to private sources.
- Use generic roles and fully synthetic examples. Replacing a name in an otherwise real incident is insufficient.
- Do not infer market results from an agent response. The operator-confirmed event ledger owns outcome counts; the model only produces work and suggestions.
- Model calls may research and prepare. Sending, posting, spending, deployment, and CRM writes require a separate, exact authorized action. This runner implements none of those transports.
- Preserve goal criteria, negative feedback, waiting states, and incomplete outcomes. Method changes cannot lower the goal or expand authority.
- Add only files in `submission-files.txt`. Run the submission checker and meaningful tests before committing. Do not include private values in commit messages.
