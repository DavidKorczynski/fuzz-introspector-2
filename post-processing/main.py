# Copyright 2021 Fuzz Introspector Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import logging
import os
import sys
import yaml
from typing import List

import analysis
import constants
import data_loader
import html_report
import utils

import datatypes.project_profile

logger = logging.getLogger(name=__name__)


def correlate_binaries_to_logs(binaries_dir: str) -> int:
    pairings = utils.scan_executables_for_fuzz_introspector_logs(binaries_dir)
    logger.info(f"Pairings: {str(pairings)}")
    with open("exe_to_fuzz_introspector_logs.yaml", "w+") as etf:
        etf.write(yaml.dump({'pairings': pairings}))
    return constants.APP_EXIT_SUCCESS


def run_analysis_on_dir(
    target_folder: str,
    coverage_url: str,
    analyses_to_run: List[str],
    correlation_file: str,
    enable_all_analyses: bool,
    report_name: str,
    language: str
) -> int:
    if enable_all_analyses:
        all_analyses = [
            "OptimalTargets",
            "RuntimeCoverageAnalysis",
            "FuzzDriverSynthesizerAnalysis",
            "FuzzEngineInputAnalysis"
        ]
        for analysis in all_analyses:
            if analysis not in analyses_to_run:
                analyses_to_run.append(analysis)

    logger.info("[+] Loading profiles")
    profiles = data_loader.load_all_profiles(target_folder, language)
    if len(profiles) == 0:
        logger.info("Found no profiles. Exiting")
        return constants.APP_EXIT_ERROR

    input_bugs = data_loader.try_load_input_bugs()
    logger.info(f"[+] Loaded {len(input_bugs)} bugs")

    logger.info("[+] Accummulating profiles")
    for profile in profiles:
        profile.accummulate_profile(target_folder)

    logger.info("[+] Correlating executables to Fuzz introspector reports")
    correlation_dict = utils.data_file_read_yaml(correlation_file)
    if correlation_dict is not None and "pairings" in correlation_dict:
        for profile in profiles:
            profile.correlate_executable_name(correlation_dict)
    else:
        logger.info("- Nothing to correlate")

    logger.info("[+] Creating project profile")
    project_profile = datatypes.project_profile.MergedProjectProfile(profiles)

    logger.info("[+] Refining profiles")
    for profile in profiles:
        profile.refine_paths(project_profile.basefolder)

    # logger.info("[+] Loading branch profiles")
    # branch_profiles = data_loader.load_all_branch_profiles(target_folder)
    # if len(branch_profiles) == 0:
    #     logger.info("[X][X] Found no branch profiles!")

    # Overlay coverage in each profile
    for profile in profiles:
        analysis.overlay_calltree_with_coverage(
            profile,
            project_profile,
            coverage_url,
            project_profile.basefolder
        )

    logger.info(f"Analyses to run: {str(analyses_to_run)}")

    logger.info("[+] Creating HTML report")
    html_report.create_html_report(
        profiles,
        project_profile,
        analyses_to_run,
        coverage_url,
        project_profile.basefolder,
        report_name
    )
    return constants.APP_EXIT_SUCCESS


def get_cmdline_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()

    subparsers = parser.add_subparsers(dest='command')

    # Report generation command
    report_parser = subparsers.add_parser(
        "report",
        help="generate fuzz-introspector HTML report",
    )
    report_parser.add_argument(
        "--target_dir",
        type=str,
        help="Directory where the data files are",
        required=True
    )
    report_parser.add_argument(
        "--coverage_url",
        type=str,
        help="URL with coverage information",
        default="/covreport/linux"
    )
    report_parser.add_argument(
        "--analyses",
        nargs="+",
        default=[
            "OptimalTargets",
            "RuntimeCoverageAnalysis",
            "FuzzEngineInputAnalysis",
            "FilePathAnalyser"
        ],
        help="Analyses to run. Available options: OptimalTargets, FuzzEngineInput"
    )
    report_parser.add_argument(
        "--enable-all-analyses",
        action='store_true',
        default=False,
        help="Enables all analyses"
    )
    report_parser.add_argument(
        "--correlation_file",
        type=str,
        default="",
        help="File with correlation data"
    )
    report_parser.add_argument(
        "--name",
        type=str,
        default="",
        help="Name of project"
    )
    report_parser.add_argument(
        "--language",
        type=str,
        default="c-cpp",
        help="Language of project"
    )

    # Command for correlating binary files to fuzzerLog files
    correlate_parser = subparsers.add_parser(
        "correlate",
        help="correlate executable files to fuzzer introspector logs"
    )
    correlate_parser.add_argument(
        "--binaries_dir",
        type=str,
        required=True,
        help="Directory with binaries to scan for Fuzz introspector tags"
    )

    return parser


def set_logging_level() -> None:
    if os.environ.get("FUZZ_LOGLEVEL"):
        level = os.environ.get("FUZZ_LOGLEVEL")
        if level == "debug":
            logging.basicConfig(level=logging.DEBUG)
        else:
            logging.basicConfig(level=logging.INFO)
    else:
        logging.basicConfig(level=logging.INFO)
    logger.info("Logging level set")
    logger.debug("Logging level set")


def main() -> int:
    logger.info("Running fuzz introspector post-processing")
    set_logging_level()

    parser = get_cmdline_parser()
    args = parser.parse_args()
    if args.command == 'report':
        return_code = run_analysis_on_dir(
            args.target_dir,
            args.coverage_url,
            args.analyses,
            args.correlation_file,
            args.enable_all_analyses,
            args.name,
            args.language
        )
        logger.info("Ending fuzz introspector report generation")
    elif args.command == 'correlate':
        return_code = correlate_binaries_to_logs(args.binaries_dir)
    else:
        return_code = constants.APP_EXIT_ERROR
    logger.info("Ending fuzz introspector post-processing")
    sys.exit(return_code)


if __name__ == "__main__":
    main()
