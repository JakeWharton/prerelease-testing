import os.path
import yaml

if __name__ == '__main__':
	with open('projects.yaml', 'r') as y:
		projects = yaml.safe_load(y)

	with open('.github/workflows/build.yaml', 'w') as f:
		f.write('''name: build

on:
  pull_request: {}
  workflow_dispatch: {}
  push:
    branches:
      - 'trunk'
  schedule:
    # 9:23 AM EST
    - cron: "23 13 * * *"

env:
  GRADLE_OPTS: "-Dorg.gradle.jvmargs=-Xmx6g -Dkotlin.incremental=false -Dorg.gradle.daemon=false -Dorg.gradle.vfs.watch=false -Dorg.gradle.logging.stacktrace=full"

jobs:
  workflow-up-to-date:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.13'
      - run: python3 generate.py
      - run: git diff --exit-code

''')

		for project, config in projects.items():
			safe_project = project.replace('/', '-')
			f.write('  ' + safe_project + ''':
    runs-on: macos-latest
    outputs:
      version: ${{ steps.version.outputs.version }}
    needs:
      - workflow-up-to-date
''')
			if 'internal_dependencies' in config:
				for dep in config['internal_dependencies']:
					safe_dep = dep.replace('/', '-')
					f.write('      - ' + safe_dep + '\n')
			f.write('''    steps:
      - uses: actions/checkout@v4
        with:
          path: this
      - uses: actions/setup-java@v4
        with:
          distribution: 'zulu'
          java-version-file: this/.github/workflows/.java-version
      - uses: actions/checkout@v4
        with:
          repository: ''' + project + '''
          path: ''' + safe_project + '''
      - name: Patch versions
        run: |
          pip install -q toml-cli
''')
			if 'external_dependencies' in config:
				for dep, key in config['external_dependencies'].items():
					f.write('''          toml set --toml-path ''' + safe_project + '''/gradle/libs.versions.toml ''' + key + ''' $(toml get --toml-path this/libs.versions.toml versions.''' + dep + ''')
''')
			if 'internal_dependencies' in config:
				for dep, key in config['internal_dependencies'].items():
					safe_dep = dep.replace('/', '-')
					f.write('''          toml set --toml-path ''' + safe_project + '''/gradle/libs.versions.toml ''' + key + ''' "${{ needs.''' + safe_dep + '''.outputs.version }}"
''')
			f.write('          cd ' + safe_project + '\n')
			patch = 'patches/' + project + '.patch'
			if os.path.exists(patch):
				f.write('          git apply ../this/' + patch + '\n')
			f.write('          git diff --patch\n')
			if 'internal_dependencies' in config:
				for dep in config['internal_dependencies']:
					safe_dep = dep.replace('/', '-')
					f.write('''      - uses: actions/download-artifact@v4
        with:
          name: ''' + safe_dep + '''-snapshot
          path: ~/.m2/repository
''')
			f.write('''      - run: ./gradlew build publishToMavenLocal
        working-directory: ''' + safe_project + '\n')
			f.write('''      - uses: actions/upload-artifact@v4
        with:
          name: ''' + safe_project + '''-snapshot
          path: ~/.m2/repository
          if-no-files-found: error
      - id: version
''')
			version = config['version']
			if 'regex' in version:
				f.write('''        run: perl -ne '/''' + version['regex'].encode('unicode_escape').decode("utf-8") + '''/ and print "version=$1",$/' ''' + safe_project + '/' + version['file'] + ' >> "$GITHUB_OUTPUT"\n')
			else:
				raise Exception("Unknown version strategy")
			f.write('\n')

		f.write('''  final-status:
    runs-on: ubuntu-latest
    needs:
''')
		for project in projects.keys():
			safe_project = project.replace('/', '-')
			f.write('      - ' + safe_project + '\n')
		f.write('''    steps:
      - name: Check
        run: |
          results=$(tr -d '\\n' <<< '${{ toJSON(needs.*.result) }}')
          if ! grep -q -v -E '(failure|cancelled)' <<< "$results"; then
            echo "One or more required jobs failed"
            exit 1
          fi
''')
