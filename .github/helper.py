#!/usr/bin/env python3

import semver
import subprocess
import sys
import tempfile
import textwrap
import tomlkit
import yaml

def main():
	if len(sys.argv) <= 1:
		print('Argument required')
		exit(1)
	match sys.argv[1]:
		case 'generate':
			generate()
		case 'patch-toml':
			if len(sys.argv) != 6:
				print('Incorrect arguments for patch-toml')
				exit(1)
			patch_toml(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
		case _:
			print('Unknown command:', sys.argv[1])
			exit(1)


def patch_toml(project_dir: str, toml_key: str, version_type: str, version_arg: str):
	match version_type:
		case 'key':
			this_toml_path = 'this/libs.versions.toml'
			with open(this_toml_path, 'r') as f:
				this_toml = tomlkit.parse(f.read())
			version = this_toml['versions'][version_arg]
		case 'value':
			version = version_arg
		case _:
			print("Unknown version type:", version_type)
			exit(1)

	project_toml_path = project_dir + '/gradle/libs.versions.toml'
	with open(project_toml_path, 'r') as f:
		project_toml = tomlkit.parse(f.read())

	if toml_key.startswith('versions.'):
		toml_versions_key = toml_key.removeprefix('versions.')
		old_version = project_toml['versions'][toml_versions_key]
		project_toml['versions'][toml_versions_key] = version
	elif toml_key.startswith('plugins.'):
		toml_plugins_key = toml_key.removeprefix('plugins.')
		old_version = project_toml['plugins'][toml_plugins_key]['version']
		project_toml['plugins'][toml_plugins_key]['version'] = version
	elif toml_key.startswith('libraries.'):
		toml_libraries_key = toml_key.removeprefix('libraries.')
		project_toml_library = project_toml['libraries'][toml_libraries_key]
		if isinstance(project_toml_library, str):
			coordinates, _, old_version = project_toml_library.rpartition(':')
			new_triple = coordinates + ':' + version
			project_toml['libraries'][toml_libraries_key] = new_triple
		else:
			old_version = project_toml_library['version']
			if isinstance(old_version, tomlkit.api.Table):
				print('Library key ', toml_libraries_key, " contains a 'version' table. Migrate to using version key.")
				exit(1)
			project_toml_library['version'] = version
	else:
		print('Unknown TOML key prefix:', toml_key)
		exit(1)

	if semver.compare(old_version, version) > 0:
		print("Refusing to downgrade", toml_key, 'from', old_version, 'to', version)
	else:
		with open(project_toml_path, 'w') as f:
			f.write(tomlkit.dumps(project_toml))


def safe_name(name: str) -> str:
	return name.replace('/', '-').replace('.', '-')


def generate():
	with open('projects.yaml', 'r') as y:
		projects = yaml.safe_load(y)

	with tempfile.NamedTemporaryFile(mode = 'w', delete = False) as f:
		f.write('direction: left\n\n')

		for project, config in projects.items():
			f.write('"' + project + '"\n')

			if 'internal_dependencies' in config:
				for dep in config['internal_dependencies'].keys():
					f.write('"' + project + '" -> "' + dep + '"\n')

		f.close()

		subprocess.run(['d2', '--layout=elk', f.name, '.github/projects.svg'])

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
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-python@v6
        with:
          python-version: '3.13'
      - run: brew update && brew install d2
      - run: pip install -r .github/requirements.txt
      - run: .github/helper.py generate
      - run: git diff --exit-code

''')

		for project, config in projects.items():
			safe_project = safe_name(project)
			f.write('  ' + safe_project + ''':
    runs-on: macos-latest
    if: ${{ !cancelled() }}
''')

			if 'version' in config:
				f.write('''    outputs:
      version: ${{ steps.version.outputs.version }}
''')

			f.write('''    needs:
      - workflow-up-to-date
''')
			if 'internal_dependencies' in config:
				for dep in config['internal_dependencies']:
					safe_dep = safe_name(dep)
					f.write('      - ' + safe_dep + '\n')

			f.write('''    steps:
      - name: "Checkout this repository"
        uses: actions/checkout@v5
        with:
          path: this
      - name: "Checkout ''' + project + ''' repository"
        uses: actions/checkout@v5
        with:
          repository: ''' + project + '''
          submodules: true
          path: ''' + safe_project + '\n')
			if 'ref' in config:
				f.write('          ref: ' + config['ref'] + '\n')
			f.write('''      - uses: actions/setup-java@v5
        with:
          distribution: 'zulu'
          java-version-file: this/.github/workflows/.java-version
      - uses: gradle/actions/setup-gradle@v5
      - uses: actions/setup-python@v6
        with:
          python-version: '3.13'
      - name: "Patch external dependencies"
        run: |
          pip install -r this/.github/requirements.txt
''')

			if 'external_dependencies' in config:
				for dep, key in config['external_dependencies'].items():
					f.write('          this/.github/helper.py patch-toml ' + safe_project + ' ' + key + ' key ' + dep + '\n')

			if 'internal_dependencies' in config:
				for dep, key in config['internal_dependencies'].items():
					safe_dep = safe_name(dep)
					f.write('      - name: "Download internal dependency ' + dep + '''"
        uses: actions/download-artifact@v5
        if: ${{ needs.''' + safe_dep + '''.outputs.version != '' }}
        with:
          name: ''' + safe_dep + '''-snapshot
          path: ~/.m2/repository
      - name: "Patch internal dependency ''' + dep + '''"
        run: this/.github/helper.py patch-toml ''' + safe_project + ' ' + key + ''' value ${{ needs.''' + safe_dep + '''.outputs.version }}
        if: ${{ needs.''' + safe_dep + '''.outputs.version != '' }}
''')

			if 'setup' in config:
				setup = yaml.dump(config['setup'])
				f.write(textwrap.indent(setup, '      '))

			f.write('''      - name: "Patch maven local"
        working-directory: ''' + safe_project + '''
        run: git grep -l 'mavenCentral()' '*.gradle*' | xargs sed -i "" -E 's/^([[:space:]]+)mavenCentral\\(\\)$/\\1mavenLocal()\\n\\1mavenCentral()/g'
      - name: "Show local change diff"
        working-directory: ''' + safe_project + '''
        run: git diff --patch
''')

			if 'version' in config:
				f.write('''      - name: "Publish ''' + safe_project + '''"
        working-directory: ''' + safe_project + '''
        run: ../this/gradlew --continue ''')
				if 'pre_build' in config:
					f.write(config['pre_build'] + ' ')
				f.write('''publishToMavenLocal
      - uses: actions/upload-artifact@v4
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

			if 'compile_only' not in config:
				f.write('''      - name: "Check ''' + safe_project + '''"
        working-directory: ''' + safe_project + '''
        run: ../this/gradlew --continue ''')
				# Only run the pre_build if we didn't already run it as part of library deploy.
				if 'pre_build' in config and 'version' not in config:
					f.write(config['pre_build'] + ' ')
				f.write('build')
				if 'post_build' in config:
					f.write(' ' + config['post_build'])
				f.write('\n')

			f.write('\n')

		f.write('''  final-status:
    if: ${{ !cancelled() }}
    runs-on: ubuntu-latest
    needs:
      - workflow-up-to-date
''')
		for project in projects.keys():
			safe_project = safe_name(project)
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


if __name__ == '__main__':
	main()
