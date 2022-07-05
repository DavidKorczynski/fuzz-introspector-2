# Copyright 2022 Fuzz Introspector Authors
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
"""Reads the data output from the fuzz introspector LLVM plugin."""

import os
import copy
import json
import logging

from typing import (
    Any,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
)

import fuzz_cfg_load
import fuzz_constants
import fuzz_cov_load
import fuzz_utils

from exceptions import DataLoaderError

logger = logging.getLogger(name=__name__)
logger.setLevel(logging.INFO)


class FuzzerProfile:
    """
    Class for storing information about a given Fuzzer.
    This class essentially holds data corresponding to the output of run of the LLVM
    plugin. That means, the output from the plugin for a single fuzzer.
    """
    def __init__(
        self,
        filename: str,
        data_dict_yaml: Dict[Any, Any],
        target_lang: str = "c-cpp"
    ) -> None:
        self.introspector_data_file = filename
        self.function_call_depths = fuzz_cfg_load.data_file_read_calltree(filename)
        self.fuzzer_source_file: str = data_dict_yaml['Fuzzer filename']
        self.binary_executable: str = ""
        self.coverage: Optional[fuzz_cov_load.CoverageProfile] = None
        self.file_targets: Dict[str, Set[str]] = dict()
        self.target_lang = target_lang

        # Create a list of all the functions.
        self.all_class_functions = dict()
        for elem in data_dict_yaml['All functions']['Elements']:
            # Check if there is normalisation issue and log if so
            if "." in elem['functionName']:
                split_name = elem['functionName'].split(".")
                if split_name[-1].isnumeric():
                    logger.info(
                        f"We may have a non-normalised function name: {elem['functionName']}"
                    )

            func_profile = FunctionProfile(elem)
            logger.debug(f"Adding {func_profile.function_name}")
            self.all_class_functions[func_profile.function_name] = func_profile

    def resolve_coverage_link(
        self,
        cov_url: str,
        source_file: str,
        lineno: int,
        function_name: str
    ) -> str:
        """Resolves a link to a coverage report."""

        # For C/CPP
        if self.target_lang == "c-cpp":
            return cov_url + source_file + ".html#L" + str(lineno)

        # For Python
        logger.debug(f"Can't get link for {cov_url} -- {source_file} -- {lineno}")

        # Temporarily for debugging purposes. TODO: David remove this later
        # Find the html_status.json file. This is a file generated by the Python
        # coverate utility and contains mappings from source to html file. We
        # need this mapping in order to create links from the data extracted
        # during AST analysis, as there we only have the source code.
        html_summaries = fuzz_utils.get_all_files_in_tree_with_regex(".", ".*html_status.json$")
        logger.info(str(html_summaries))
        if len(html_summaries) > 0:
            html_idx = html_summaries[0]
            with open(html_idx, "r") as jf:
                data = json.load(jf)
            for fl in data['files']:
                found_target = fuzz_utils.approximate_python_coverage_files(
                    function_name,
                    data['files'][fl]['index']['relative_filename'],
                )
                if found_target:
                    return cov_url + "/" + fl + ".html" + "#t" + str(lineno)
        else:
            logger.info("Could not find any html_status.json file")
        return "#"

    def refine_paths(self, basefolder: str) -> None:
        """
        Removes the project_profile's basefolder from source paths in a given profile.
        """
        # Only do this is basefolder is not wrong
        if basefolder == "/":
            return

        self.fuzzer_source_file = self.fuzzer_source_file.replace(basefolder, "")

        if self.function_call_depths is not None:
            all_callsites = fuzz_cfg_load.extract_all_callsites(self.function_call_depths)
            for cs in all_callsites:
                cs.dst_function_source_file = cs.dst_function_source_file.replace(basefolder, "")

            new_dict = {}
            for key in self.file_targets:
                new_dict[key.replace(basefolder, "")] = self.file_targets[key]
            self.file_targets = new_dict

    def set_all_reached_functions(self) -> None:
        """
        sets self.functions_reached_by_fuzzer to all functions reached
        by LLVMFuzzerTestOneInput
        """
        if "LLVMFuzzerTestOneInput" in self.all_class_functions:
            self.functions_reached_by_fuzzer = (
                self.all_class_functions["LLVMFuzzerTestOneInput"].functions_reached
            )
            return

        # Find Python entrypoint
        for func_name in self.all_class_functions:
            if "TestOneInput" in func_name:
                reached = self.all_class_functions[func_name].functions_reached
                self.functions_reached_by_fuzzer = reached
                return

        # TODO: make fuzz-introspector exceptions
        raise Exception

    def reaches_file(
        self,
        file_name: str,
        basefolder: Optional[str] = None
    ) -> bool:
        if basefolder is not None:
            new_file_name = file_name.replace(basefolder, "")
        else:
            new_file_name = file_name

        for ff in self.file_targets:
            logger.info(f"\t{ff}")

        # Only some file paths have removed base folder. We must check for both.
        return (file_name in self.file_targets) or (new_file_name in self.file_targets)

    def reaches(self, func_name: str) -> bool:
        return func_name in self.functions_reached_by_fuzzer

    def correlate_executable_name(self, correlation_dict) -> None:
        for elem in correlation_dict['pairings']:
            if os.path.basename(self.introspector_data_file) in f"{elem['fuzzer_log_file']}.data":
                self.binary_executable = str(elem['executable_path'])

                lval = os.path.basename(self.introspector_data_file)
                rval = f"{elem['fuzzer_log_file']}.data"
                logger.info(f"Correlated {lval} with {rval}")

    def get_key(self) -> str:
        """
        Returns the "key" we use to identify this Fuzzer profile.
        """
        if self.binary_executable != "":
            return os.path.basename(self.binary_executable)

        return self.fuzzer_source_file

    def set_all_unreached_functions(self) -> None:
        """
        sets self.functions_unreached_by_fuzzer to all functiosn in self.all_class_functions
        that are not in self.functions_reached_by_fuzzer
        """
        self.functions_unreached_by_fuzzer = [
            f.function_name for f
            in self.all_class_functions.values()
            if f.function_name not in self.functions_reached_by_fuzzer
        ]

    def load_coverage(self, target_folder: str) -> None:
        """Load coverage data for this profile"""
        logger.info(f"Loading coverage of type {self.target_lang}")
        if self.target_lang == "c-cpp":
            self.coverage = fuzz_cov_load.llvm_cov_load(
                target_folder,
                self.get_target_fuzzer_filename()
            )
        elif self.target_lang == "python":
            self.coverage = fuzz_cov_load.load_python_json_cov(
                target_folder
            )
        else:
            raise DataLoaderError(
                "The profile target has no coverage loading support"
            )

    def get_target_fuzzer_filename(self) -> str:
        return self.fuzzer_source_file.split("/")[-1].replace(".cpp", "").replace(".c", "")

    def get_file_targets(self) -> None:
        """
        Sets self.file_targets to be a dictionarty of string to string.
        Each key in the dictionary is a filename and the corresponding value is
        a set of strings containing strings which are the names of the functions
        in the given file that are reached by the fuzzer.
        """
        if self.function_call_depths is not None:
            all_callsites = fuzz_cfg_load.extract_all_callsites(self.function_call_depths)
            for cs in all_callsites:
                if cs.dst_function_source_file.replace(" ", "") == "":
                    continue
                if cs.dst_function_source_file not in self.file_targets:
                    self.file_targets[cs.dst_function_source_file] = set()
                self.file_targets[cs.dst_function_source_file].add(cs.dst_function_name)

    def get_total_basic_blocks(self) -> None:
        """
        sets self.total_basic_blocks to the sym of basic blocks of all the functions
        reached by this fuzzer.
        """
        total_basic_blocks = 0
        for func in self.functions_reached_by_fuzzer:
            fd = self.all_class_functions[func]
            total_basic_blocks += fd.bb_count
        self.total_basic_blocks = total_basic_blocks

    def get_total_cyclomatic_complexity(self) -> None:
        """
        sets self.total_cyclomatic_complexity to the sum of cyclomatic complexity
        of all functions reached by this fuzzer.
        """
        self.total_cyclomatic_complexity = 0
        for func in self.functions_reached_by_fuzzer:
            fd = self.all_class_functions[func]
            self.total_cyclomatic_complexity += fd.cyclomatic_complexity

    def accummulate_profile(self, target_folder: str) -> None:
        """
        Triggers various analyses on the data of the fuzzer. This is used after a
        profile has been initialised to generate more interesting data.
        """
        self.set_all_reached_functions()
        self.set_all_unreached_functions()
        self.load_coverage(target_folder)
        self.get_file_targets()
        self.get_total_basic_blocks()
        self.get_total_cyclomatic_complexity()

    def get_cov_uncovered_reachable_funcs(self) -> List[str]:
        if self.coverage is None:
            return []

        uncovered_funcs = []
        for funcname in self.functions_reached_by_fuzzer:
            total_func_lines, hit_lines, hit_percentage = self.get_cov_metrics(funcname)
            if total_func_lines is None:
                uncovered_funcs.append(funcname)
                continue
            if hit_lines == 0:
                uncovered_funcs.append(funcname)
        return uncovered_funcs

    def is_file_covered(
        self,
        file_name: str,
        basefolder: Optional[str] = None
    ) -> bool:
        # We need to refine the pathname to match how coverage file paths are.
        file_name = os.path.abspath(file_name)

        # Refine filename if needed
        if basefolder is not None and basefolder != "/":
            new_file_name = file_name.replace(basefolder, "")
        else:
            new_file_name = file_name

        for funcname in self.all_class_functions:
            # Check it's a relevant filename
            func_file_name = self.all_class_functions[funcname].function_source_file
            if basefolder is not None and basefolder != "/":
                new_func_file_name = func_file_name.replace(basefolder, "")
            else:
                new_func_file_name = func_file_name
            if func_file_name != file_name and new_func_file_name != new_file_name:
                continue
            # Return true if the function is hit
            tf, hl, hp = self.get_cov_metrics(funcname)
            if hp is not None and hp > 0.0:
                if func_file_name in self.file_targets or new_file_name in self.file_targets:
                    return True
        return False

    def get_cov_metrics(
        self,
        funcname: str
    ) -> Tuple[Optional[int], Optional[int], Optional[float]]:
        if self.coverage is None:
            return None, None, None
        try:
            total_func_lines, hit_lines = self.coverage.get_hit_summary(funcname)
            if total_func_lines is None or hit_lines is None:
                return None, None, None

            hit_percentage = (hit_lines / total_func_lines) * 100.0
            return total_func_lines, hit_lines, hit_percentage
        except Exception:
            return None, None, None

    def write_stats_to_summary_file(self) -> None:
        file_target_count = len(self.file_targets) if self.file_targets is not None else 0
        fuzz_utils.write_to_summary_file(
            self.get_key(),
            "stats",
            {
                "total-basic-blocks": self.total_basic_blocks,
                "total-cyclomatic-complexity": self.total_cyclomatic_complexity,
                "file-target-count": file_target_count,
            }
        )
