# Security

Strivo runs on your home network and holds pairing credentials for your streaming boxes, so security matters even for a small project.

## Reporting a vulnerability

Please **don't** open a public issue. Use GitHub's **[private vulnerability reporting](https://github.com/nickdnj/strivo/security/advisories/new)** instead. We'll acknowledge within a week.

## How Strivo is meant to be run

- On your **home network only**. Don't expose Strivo's port to the internet. For remote access, use a private network like Tailscale or WireGuard.
- Pairing credentials and your TMDB key live in the data folder. Keep it private and back it up somewhere safe.
- Strivo never sends your watch history anywhere. Only show and movie titles go to TMDB.
