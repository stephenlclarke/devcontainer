'use strict';

const path = require('node:path');
const { spawnSync } = require('node:child_process');

const workspace = process.env.GITHUB_WORKSPACE;
const artifactName = process.env.INPUT_NAME;
const artifactPaths = process.env.INPUT_PATH;

if (!workspace || !artifactName || !artifactPaths) {
  process.stderr.write('workspace, artifact name and artifact paths are required\n');
  process.exit(64);
}

const uploader = path.join(
  workspace,
  'Tools',
  'ci',
  'upload-artifact-pinned.sh'
);
const result = spawnSync(uploader, [artifactName, artifactPaths], {
  cwd: workspace,
  env: process.env,
  stdio: 'inherit'
});

if (result.error) {
  process.stderr.write(`${result.error.message}\n`);
  process.exit(1);
}

process.exit(result.status ?? 1);
