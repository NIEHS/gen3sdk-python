import requests, json, fnmatch, os, os.path, sys, subprocess, glob, ntpath, copy, re
import pandas as pd

# from pandas.tools.plotting import table
from pandas.io.json import json_normalize
from collections import Counter

import gen3
from gen3.auth import Gen3Auth
from gen3.submission import Gen3Submission

import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns

from IPython.display import display, HTML

auth = Gen3Auth(api, refresh_file=creds)
sub = Gen3Submission(api, auth)


### AWS S3 Tools:
def s3_ls(path, bucket, profile, pattern="*"):
    """Print the results of an `aws s3 ls` command"""
    s3_path = bucket + path
    cmd = ["aws", "s3", "ls", s3_path, "--profile", profile]
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode("UTF-8")
    except Exception as e:
        output = e.output.decode("UTF-8")
        print("ERROR:" + output)
    psearch = output.split("\n")
    if pattern != "*":
        pmatch = fnmatch.filter(
            psearch, pattern
        )  # if default '*', all files will match
        return arrayTable(pmatch)
    else:
        return output


def s3_files(path, bucket, profile, pattern="*", verbose=True):
    """Get a list of files returned by an `aws s3 ls` command"""
    s3_path = bucket + path
    cmd = ["aws", "s3", "ls", s3_path, "--profile", profile]
    try:
        output = subprocess.check_output(
            cmd, stderr=subprocess.STDOUT, shell=True
        ).decode("UTF-8")
    except Exception as e:
        output = e.output.decode("UTF-8")
        print("ERROR:" + output)
    output = [line.split() for line in output.split("\n")]
    output = [
        line for line in output if len(line) == 4
    ]  # filter output for lines with file info
    output = [line[3] for line in output]  # grab the filename only
    output = fnmatch.filter(output, pattern)  # if default '*', all files will match
    if verbose is True:
        print("\nIndex \t Filename")
        for i, item in enumerate(output, start=0):
            print(i, "\t", item)
    return output


def get_s3_files(path, bucket, profile, files=None, mydir=None):
    """Transfer data from object storage to the VM in the private subnet"""

    # Set the path to the directory where files reside
    s3_path = bucket + path

    # Create folder on VM for downloaded files
    if not isinstance(mydir, str):
        mydir = path
    if not os.path.exists(mydir):
        os.makedirs(mydir)

    # If files is an array of filenames, download them
    if isinstance(files, list):
        print("Getting files...")
        for filename in files:
            s3_filepath = s3_path + str(filename)
            if os.path.exists(mydir + str(filename)):
                print("File " + filename + " already downloaded in that location.")
            else:
                print(s3_filepath)
                cmd = ["aws", "s3", "--profile", profile, "cp", s3_filepath, mydir]
                try:
                    output = subprocess.check_output(
                        cmd, stderr=subprocess.STDOUT, shell=True
                    ).decode("UTF-8")
                except Exception as e:
                    output = e.output.decode("UTF-8")
                    print("ERROR:" + output)
    # If files is None, which syncs the s3_path 'directory'
    else:
        print("Syncing directory " + s3_path)
        cmd = ["aws", "s3", "--profile", profile, "sync", s3_path, mydir]
        try:
            output = subprocess.check_output(
                cmd, stderr=subprocess.STDOUT, shell=True
            ).decode("UTF-8")
        except Exception as e:
            output = e.output.decode("UTF-8")
            print("ERROR:" + output)
    print("Finished")


# Functions for downloading metadata in TSVs
# Get a list of project_ids
def get_project_ids(node=None, name=None):
    project_ids = []
    queries = []
    # Return all project_ids in the data commons if no node is provided or if node is program but no name provided
    if name is None and ((node is None) or (node is "program")):
        print("Getting all project_ids you have access to in the data commons.")
        if node == "program":
            print(
                "Specify a list of program names (name = ['myprogram1','myprogram2']) to get only project_ids in particular programs."
            )
        queries.append("""{project (first:0){project_id}}""")
    elif name is not None and node == "program":
        if isinstance(name, list):
            print("Getting all project_ids in the programs '" + ",".join(name) + "'")
            for program_name in name:
                queries.append(
                    """{project (first:0, with_path_to:{type:"program",name:"%s"}){project_id}}"""
                    % (program_name)
                )
        elif isinstance(name, str):
            print("Getting all project_ids in the program '" + name + "'")
            queries.append(
                """{project (first:0, with_path_to:{type:"program",name:"%s"}){project_id}}"""
                % (name)
            )
    elif isinstance(node, str) and isinstance(name, str):
        print(
            "Getting all project_ids for projects with a path to record '"
            + name
            + "' in node '"
            + node
            + "'"
        )
        queries.append(
            """{project (first:0, with_path_to:{type:"%s",submitter_id:"%s"}){project_id}}"""
            % (node, name)
        )
    elif isinstance(node, str) and name is None:
        print(
            "Getting all project_ids for projects with at least one record in the node '"
            + node
            + "'"
        )
        query = """{node (first:0,of_type:"%s"){project_id}}""" % (node)
        df = json_normalize(sub.query(query)["data"]["node"])
        project_ids = project_ids + list(set(df["project_id"]))
    if len(queries) > 0:
        for query in queries:
            res = sub.query(query)
            df = json_normalize(res["data"]["project"])
            project_ids = project_ids + list(set(df["project_id"]))
    return sorted(project_ids, key=str.lower)


# Create master TSV of data from each project per node
def get_node_tsvs(node, projects=None, overwrite=False, remove_empty=True):
    """Gets a TSV of the structuerd data from each node specified for each project specified
       Also creates a master TSV per node of merged data from each project.
       Returns a DataFrame containing the merged data.

    Args:
        node (str): The name of the node to download structured data from.
        projects (list): The projects to download the node from. If "None", downloads data from each project user has access to.

    Example:
    >>> df = get_node_tsvs('demographic')

    """
    if not isinstance(node, str):  # Create folder on VM for downloaded files
        mydir = "downloaded_tsvs"
    else:
        mydir = str(node + "_tsvs")

    if not os.path.exists(mydir):
        os.makedirs(mydir)
    if projects is None:  # if no projects specified, get node for all projects
        projects = list(
            json_normalize(
                sub.query("""{project (first:0){project_id}}""")["data"]["project"]
            )["project_id"]
        )
    elif isinstance(projects, str):
        projects = [projects]

    dfs = []
    df_len = 0
    for project in projects:
        filename = str(mydir + "/" + project + "_" + node + ".tsv")
        if (os.path.isfile(filename)) and (overwrite is False):
            print("File previously downloaded.")
        else:
            prog, proj = project.split("-", 1)
            sub.export_node(prog, proj, node, "tsv", filename)
        df1 = pd.read_csv(filename, sep="\t", header=0, index_col=False)
        dfs.append(df1)
        df_len += len(df1)
        print(filename + " has " + str(len(df1)) + " records.")

        if remove_empty is True:
            if df1.empty:
                print("Removing empty file: " + filename)
                cmd = ["rm", filename]  # look in the download directory
                try:
                    output = subprocess.check_output(
                        cmd, stderr=subprocess.STDOUT
                    ).decode("UTF-8")
                except Exception as e:
                    output = e.output.decode("UTF-8")
                    print("ERROR deleting file: " + output)

    all_data = pd.concat(dfs, ignore_index=True)
    print("length of all dfs: " + str(df_len))
    nodefile = str("master_" + node + ".tsv")
    all_data.to_csv(str(mydir + "/" + nodefile), sep="\t", index=False)
    print(
        "Master node TSV with "
        + str(len(all_data))
        + " total records written to "
        + nodefile
        + "."
    )
    return all_data


def get_project_tsvs(projects):
    # Get a TSV for every node in a project
    all_nodes = sorted(
        list(
            set(
                json_normalize(
                    sub.query("""{_node_type (first:-1) {id}}""")["data"]["_node_type"]
                )["id"]
            )
        )
    )  # get all the 'node_id's in the data model
    remove_nodes = [
        "program",
        "project",
        "root",
        "data_release",
    ]  # remove these nodes from list of nodes
    for node in remove_nodes:
        if node in all_nodes:
            all_nodes.remove(node)
    if isinstance(projects, str):
        projects = [projects]
    for project_id in projects:
        mydir = str(
            "project_tsvs/" + project_id + "_tsvs"
        )  # create the directory to store TSVs
        if not os.path.exists(mydir):
            os.makedirs(mydir)
        for node in all_nodes:
            query_txt = """{_%s_count (project_id:"%s")}""" % (node, project_id)
            #            query_txt = """{%s (first:1,project_id:"%s"){project_id}}""" % (node,project_id) #check for at least one record in project's node, else skip download; this query is very slightly faster than _node_count query, so use this if times-out (and other commented 'if' line below)
            res = sub.query(query_txt)
            count = res["data"][str("_" + node + "_count")]
            print(
                str(count)
                + " records found in node "
                + node
                + " in project "
                + project_id
            )
            #            if len(res['data'][node]) > 0: #using direct `node_id (first: 1)` type query
            if count > 0:
                filename = str(mydir + "/" + project_id + "_" + node + ".tsv")
                if os.path.isfile(filename):
                    print("Previously downloaded " + filename)
                else:
                    prog, proj = project_id.split("-", 1)
                    sub.export_node(prog, proj, node, "tsv", filename)
                    print(filename + " exported to " + mydir)
            else:
                print("Skipping empty node " + node + " for project " + project_id)
    cmd = ["ls", mydir]  # look in the download directory
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode("UTF-8")
    except Exception as e:
        output = "ERROR:" + e.output.decode("UTF-8")
    return output


def get_project_tsvs_faster(projects):
    # Get a TSV for every node in a project
    all_nodes = sorted(
        list(
            set(
                json_normalize(
                    sub.query("""{_node_type (first:-1) {id}}""")["data"]["_node_type"]
                )["id"]
            )
        )
    )  # get all the 'node_id's in the data model
    remove_nodes = [
        "program",
        "project",
        "root",
        "data_release",
    ]  # remove these nodes from list of nodes
    for node in remove_nodes:
        if node in all_nodes:
            all_nodes.remove(node)
    if isinstance(projects, str):
        projects = [projects]
    for project_id in projects:
        mydir = str(
            "project_tsvs/" + project_id + "_tsvs"
        )  # create the directory to store TSVs
        if not os.path.exists(mydir):
            os.makedirs(mydir)
        for node in all_nodes:
            #            query_txt = """{_%s_count (project_id:"%s")}""" % (node,project_id)
            query_txt = """{%s (first:1,project_id:"%s"){project_id}}""" % (
                node,
                project_id,
            )  # check for at least one record in project's node, else skip download
            res = sub.query(query_txt)
            #            count = res['data'][str('_'+node+'_count')]
            #            print(str(count) + ' records found in node ' + node + ' in project ' + project_id)
            if (
                len(res["data"][node]) > 0
            ):  # using direct `node_id (first: 1)` type query
                #            if count > 0:
                filename = str(mydir + "/" + project_id + "_" + node + ".tsv")
                if os.path.isfile(filename):
                    print("Previously downloaded " + filename)
                else:
                    prog, proj = project_id.split("-", 1)
                    sub.export_node(prog, proj, node, "tsv", filename)
                    print(filename + " exported to " + mydir)
            else:
                print("Skipping empty node " + node + " for project " + project_id)
    cmd = ["ls", mydir]  # look in the download directory
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode("UTF-8")
    except Exception as e:
        output = "ERROR:" + e.output.decode("UTF-8")
    return output


def delete_node(node, project):
    failure = []
    success = []
    results = {}

    query = """{_%s_count (project_id:"%s") %s (first: 0, project_id:"%s"){id}}""" % (
        node,
        project,
        node,
        project,
    )

    res = sub.query(query)
    ids = [x["id"] for x in res["data"][node]]

    for uuid in ids:
        r = json.loads(sub.delete_record(program, project, uuid))
        code = r["code"]
        if code == 200:
            print("Deleted record: " + uuid)
            success.append(uuid)
        else:
            print("Failed to delete: " + uuid + ", code: " + code)
            print(r.text)
            failure.append(uuid)
    results["failure"] = failure
    results["success"] = success
    return results


def delete_records(uuids, project_id):
    ## Delete a list of records in 'uuids' from a project
    program, project = project_id.split("-", 1)
    failure = []
    success = []
    results = {}
    if isinstance(uuids, str):
        uuids = [uuids]
    if isinstance(uuids, list):
        for uuid in uuids:
            r = json.loads(sub.delete_record(program, project, uuid))
            if r["code"] == 200:
                print("Deleted record id: " + uuid)
                success.append(uuid)
            else:
                print("Could not delete record id: " + uuid)
                print("API Response: " + str(r["code"]))
                failure.append(uuid)
    results["failure"] = failure
    results["success"] = success
    return results


def node_record_counts(project_id):
    query_txt = """{node (first:-1, project_id:"%s"){type}}""" % (project_id)
    res = sub.query(query_txt)
    df = json_normalize(res["data"]["node"])
    counts = Counter(df["type"])
    df = pd.DataFrame.from_dict(counts, orient="index").reset_index()
    df = df.rename(columns={"index": "node", 0: "count"})
    display(df)
    return df


def list_project_files(project_id):
    query_txt = (
        """{datanode(first:-1,project_id: "%s") {type file_name id object_id}}"""
        % (project_id)
    )
    res = sub.query(query_txt)
    if len(res["data"]["datanode"]) == 0:
        print("Project " + project_id + " has no records in any data_file node.")
        return None
    else:
        df = json_normalize(res["data"]["datanode"])
        json_normalize(Counter(df["type"]))
        # guids = df.loc[(df['type'] == node)]['object_id']
        return df


def get_data_file_tsvs(projects=None, remove_empty=True):
    # Download TSVs for all data file nodes in the specified projects
    # if no projects specified, get node for all projects
    if projects is None:
        projects = list(
            json_normalize(
                sub.query("""{project (first:0){project_id}}""")["data"]["project"]
            )["project_id"]
        )
    elif isinstance(projects, str):
        projects = [projects]
    # Make a directory for files
    mydir = "downloaded_data_file_tsvs"
    if not os.path.exists(mydir):
        os.makedirs(mydir)
    # list all data_file 'node_id's in the data model
    dnodes = list(
        set(
            json_normalize(
                sub.query("""{_node_type (first:-1,category:"data_file") {id}}""")[
                    "data"
                ]["_node_type"]
            )["id"]
        )
    )
    mnodes = list(
        set(
            json_normalize(
                sub.query("""{_node_type (first:-1,category:"metadata_file") {id}}""")[
                    "data"
                ]["_node_type"]
            )["id"]
        )
    )
    inodes = list(
        set(
            json_normalize(
                sub.query("""{_node_type (first:-1,category:"index_file") {id}}""")[
                    "data"
                ]["_node_type"]
            )["id"]
        )
    )
    nodes = list(set(dnodes + mnodes + inodes))
    # get TSVs and return a master pandas DataFrame with records from every project
    dfs = []
    df_len = 0
    for node in nodes:
        for project in projects:
            filename = str(mydir + "/" + project + "_" + node + ".tsv")
            if os.path.isfile(filename):
                print("\n" + filename + " previously downloaded.")
            else:
                prog, proj = project.split("-", 1)
                sub.export_node(
                    prog, proj, node, "tsv", filename
                )  # use the gen3sdk to download a tsv for the node
            df1 = pd.read_csv(
                filename, sep="\t", header=0
            )  # read in the downloaded TSV to append to the master (all projects) TSV
            dfs.append(df1)
            df_len += len(df1)  # Counting the total number of records in the node
            print(filename + " has " + str(len(df1)) + " records.")
            if remove_empty is True:
                if df1.empty:
                    print("Removing empty file: " + filename)
                    cmd = ["rm", filename]  # look in the download directory
                    try:
                        output = subprocess.check_output(
                            cmd, stderr=subprocess.STDOUT
                        ).decode("UTF-8")
                    except Exception as e:
                        output = e.output.decode("UTF-8")
                        print("ERROR:" + output)
        all_data = pd.concat(dfs, ignore_index=True, sort=False)
        print(
            "\nlength of all dfs: " + str(df_len)
        )  # this should match len(all_data) below
        nodefile = str("master_" + node + ".tsv")
        all_data.to_csv(str(mydir + "/" + nodefile), sep="\t")
        print(
            "Master node TSV with "
            + str(len(all_data))
            + " total records written to "
            + nodefile
            + "."
        )  # this should match df_len above
    return all_data


def list_guids_in_nodes(nodes=None, projects=None):
    # Get GUIDs for node(s) in project(s)
    if (
        nodes is None
    ):  # get all data_file/metadata_file/index_file 'node_id's in the data model
        categories = ["data_file", "metadata_file", "index_file"]
        nodes = []
        for category in categories:
            query_txt = """{_node_type (first:-1,category:"%s") {id}}""" % category
            df = json_normalize(sub.query(query_txt)["data"]["_node_type"])
            if not df.empty:
                nodes = list(set(nodes + list(set(df["id"]))))
    elif isinstance(nodes, str):
        nodes = [nodes]
    if projects is None:
        projects = list(
            json_normalize(
                sub.query("""{project (first:0){project_id}}""")["data"]["project"]
            )["project_id"]
        )
    elif isinstance(projects, str):
        projects = [projects]
    all_guids = (
        {}
    )  # all_guids will be a nested dict: {project_id: {node1:[guids1],node2:[guids2]} }
    for project in projects:
        all_guids[project] = {}
        for node in nodes:
            guids = []
            query_txt = (
                """{%s (first:-1,project_id:"%s") {project_id file_size file_name object_id id}}"""
                % (node, project)
            )
            res = sub.query(query_txt)
            if len(res["data"][node]) == 0:
                print(project + " has no records in node " + node + ".")
                guids = None
            else:
                df = json_normalize(res["data"][node])
                guids = list(df["object_id"])
                print(
                    project
                    + " has "
                    + str(len(guids))
                    + " records in node "
                    + node
                    + "."
                )
            all_guids[project][node] = guids
            # nested dict: all_guids[project][node]
    return all_guids


def download_files_by_guids(guids=None):
    # Make a directory for files
    mydir = "downloaded_data_files"
    file_names = {}
    if not os.path.exists(mydir):
        os.makedirs(mydir)
    if isinstance(guids, str):
        guids = [guids]
    if isinstance(guids, list):
        for guid in guids:
            cmd = (
                client
                + " download-single --filename-format=combined --profile="
                + profile
                + " --guid="
                + guid
            )
            try:
                output = subprocess.check_output(
                    cmd, stderr=subprocess.STDOUT, shell=True
                ).decode("UTF-8")
                try:
                    file_name = re.search(
                        "Successfully downloaded (.+)\\n", output
                    ).group(1)
                    cmd = "mv " + file_name + " " + mydir
                    try:
                        output = subprocess.check_output(
                            cmd, stderr=subprocess.STDOUT, shell=True
                        ).decode("UTF-8")
                    except Exception as e:
                        output = e.output.decode("UTF-8")
                        print("ERROR:" + output)
                except AttributeError:
                    file_name = ""  # apply your error handling
                print("Successfully downloaded: " + file_name)
                file_names[guid] = file_name
            except Exception as e:
                print("ERROR:" + str(output) + str(e))
    else:
        print(
            'Provide a list of guids to download: "get_file_by_guid(guids=guid_list)"'
        )
    return file_names


def get_records_for_uuids(ids, project, api):
    dfs = []
    for uuid in ids:
        # sub.export_record("DCF", "CCLE", "d70b41b9-6f90-4714-8420-e043ab8b77b9", "json", filename="DCF-CCLE_one_record.json")
        # export_record(self, program, project, uuid, fileformat, filename=None)
        mydir = str(
            "project_uuids/" + project + "_tsvs"
        )  # create the directory to store TSVs
        if not os.path.exists(mydir):
            os.makedirs(mydir)
        filename = str(mydir + "/" + project + "_" + uuid + ".tsv")
        if os.path.isfile(filename):
            print("File previously downloaded.")
        else:
            prog, proj = project.split("-", 1)
            sub.export_record(prog, proj, uuid, "tsv", filename)
        df1 = pd.read_csv(filename, sep="\t", header=0)
        dfs.append(df1)
    all_data = pd.concat(dfs, ignore_index=True)
    master = str("master_uuids_" + project + ".tsv")
    all_data.to_csv(str(mydir + "/" + master), sep="\t")
    print(
        "Master node TSV with "
        + str(len(all_data))
        + " total records written to "
        + master
        + "."
    )
    return all_data


def find_duplicate_filenames(node, project):
    # download the node
    df = get_node_tsvs(node, project, overwrite=True)
    counts = Counter(df["file_name"])
    count_df = pd.DataFrame.from_dict(counts, orient="index").reset_index()
    count_df = count_df.rename(columns={"index": "file_name", 0: "count"})
    dup_df = count_df.loc[count_df["count"] > 1]
    dup_files = list(dup_df["file_name"])
    dups = df[df["file_name"].isin(dup_files)].sort_values(by="md5sum", ascending=False)
    return dups


def paginate_query(node, project_id, props=["id", "submitter_id"], chunk_size=1000):
    program, project = project_id.split("-", 1)
    properties = " ".join(map(str, props))
    # get size of query:
    query_txt = """{_%s_count (project_id:"%s")}""" % (node, project_id)
    res = sub.query(query_txt)
    count_name = "_".join(map(str, ["", node, "count"]))
    qsize = res["data"][count_name]
    print("Total of " + str(qsize) + " records in node " + node)
    offset = 0
    dfs = []
    df = pd.DataFrame()
    while offset < qsize:
        print("Offset set to: " + str(offset))
        query_txt = """{%s (first: %s, offset: %s, project_id:"%s"){%s}}""" % (
            node,
            chunk_size,
            offset,
            project_id,
            properties,
        )
        res = sub.query(query_txt)
        df1 = json_normalize(res["data"][node])
        dfs.append(df1)
        offset += chunk_size
    if len(dfs) > 0:
        df = pd.concat(dfs, ignore_index=True)
    return df


def get_duplicates(nodes, projects, api):
    # Get duplicate SUBMITTER_IDs in a node, which SHOULD NEVER HAPPEN but alas it has, thus this script
    # if no projects specified, get node for all projects
    if projects is None:
        projects = list(
            json_normalize(
                sub.query("""{project (first:0){project_id}}""")["data"]["project"]
            )["project_id"]
        )
    elif isinstance(projects, str):
        projects = [projects]

    # if no nodes specified, get all nodes in data commons
    if nodes is None:
        all_nodes = sorted(
            list(
                set(
                    json_normalize(
                        sub.query("""{_node_type (first:-1) {id}}""")["data"][
                            "_node_type"
                        ]
                    )["id"]
                )
            )
        )  # get all the 'node_id's in the data model
        remove_nodes = [
            "program",
            "project",
            "root",
            "data_release",
        ]  # remove these nodes from list of nodes
        for node in remove_nodes:
            if node in all_nodes:
                all_nodes.remove(node)
        nodes = all_nodes
    elif isinstance(nodes, str):
        nodes = [nodes]

    pdups = {}
    for project_id in projects:
        pdups[project_id] = {}
        print("Getting duplicates in project " + project_id)
        for node in nodes:
            print("\tChecking " + node + " node")
            df = paginate_query(
                node=node,
                project_id=project_id,
                props=["id", "submitter_id"],
                chunk_size=1000,
            )
            if not df.empty:
                counts = Counter(df["submitter_id"])
                c = pd.DataFrame.from_dict(counts, orient="index").reset_index()
                c = c.rename(columns={"index": "submitter_id", 0: "count"})
                dupc = c.loc[c["count"] > 1]
                if not dupc.empty:
                    dups = list(set(dupc["submitter_id"]))
                    ids = {}
                    for sid in dups:
                        ids[sid] = list(df.loc[df["submitter_id"] == sid]["id"])
                    pdups[project_id][node] = ids
    return pdups


def delete_duplicates(dups, project_id, api):
    if not isinstance(dups, dict):
        print(
            "Must provide duplicates as a dictionary of keys:submitter_ids and values:ids; use get_duplicates function"
        )

    program, project = project_id.split("-", 1)
    failure = []
    success = []
    results = {}
    sids = list(dups.keys())
    total = len(sids)
    count = 1
    for sid in sids:
        while len(dups[sid]) > 1:
            uuid = dups[sid].pop(1)
            r = json.loads(sub.delete_record(program, project, uuid))
            if r["code"] == 200:
                print(
                    "Deleted record id (" + str(count) + "/" + str(total) + "): " + uuid
                )
                success.append(uuid)
            else:
                print("Could not deleted record id: " + uuid)
                print("API Response: " + r["code"])
                failure.append(uuid)
        results["failure"] = failure
        results["success"] = success
        count += 1
    return results


def query_records(node, project_id, api, chunk_size=500):
    # Using paginated query, Download all data in a node as a DataFrame and save as TSV
    schema = sub.get_dictionary_node(node)
    props = list(schema["properties"].keys())
    links = list(schema["links"])
    # need to get links out of the list of properties because they're handled differently in the query
    link_names = []
    for link in links:
        link_list = list(link)
        if "subgroup" in link_list:
            subgroup = link["subgroup"]
            for sublink in subgroup:
                link_names.append(sublink["name"])
        else:
            link_names.append(link["name"])
    for link in link_names:
        if link in props:
            props.remove(link)
            props.append(str(link + "{id submitter_id}"))

    df = paginate_query(node, project_id, props, chunk_size)
    outfile = "_".join(project_id, node, "query.tsv")
    df.to_csv(outfile, sep="\t", index=False, encoding="utf-8")
    return df


# Group entities in details into succeeded (successfully created/updated) and failed valid/invalid
def summarize_submission(tsv, details, write_tsvs):
    with open(details, "r") as file:
        f = file.read().rstrip("\n")
    chunks = f.split("\n\n")
    invalid = []
    messages = []
    valid = []
    succeeded = []
    responses = []
    results = {}
    chunk_count = 1
    for chunk in chunks:
        d = json.loads(chunk)
        if "code" in d and d["code"] != 200:
            entities = d["entities"]
            response = str(
                "Chunk "
                + str(chunk_count)
                + " Failed: "
                + str(len(entities))
                + " entities."
            )
            responses.append(response)
            for entity in entities:
                sid = entity["unique_keys"][0]["submitter_id"]
                if entity["valid"]:  # valid but failed
                    valid.append(sid)
                else:  # invalid and failed
                    message = entity["errors"][0]["message"]
                    messages.append(message)
                    invalid.append(sid)
                    print("Invalid record: " + sid + "\n\tmessage: " + message)
        elif "code" not in d:
            responses.append("Chunk " + str(chunk_count) + " Timed-Out: " + str(d))
        else:
            entities = d["entities"]
            response = str(
                "Chunk "
                + str(chunk_count)
                + " Succeeded: "
                + str(len(entities))
                + " entities."
            )
            responses.append(response)
            for entity in entities:
                sid = entity["unique_keys"][0]["submitter_id"]
                succeeded.append(sid)
        chunk_count += 1
    results["valid"] = valid
    results["invalid"] = invalid
    results["messages"] = messages
    results["succeeded"] = succeeded
    results["responses"] = responses
    submitted = succeeded + valid + invalid  # 1231 in test data
    # get records missing in details from the submission.tsv
    df = pd.read_csv(tsv, sep="\t", header=0)
    missing_df = df.loc[
        ~df["submitter_id"].isin(submitted)
    ]  # these are records that timed-out, 240 in test data
    missing = list(missing_df["submitter_id"])
    results["missing"] = missing

    # Find the rows in submitted TSV that are not in either failed or succeeded, 8 time outs in test data, 8*30 = 240 records
    if write_tsvs is True:
        print("Writing TSVs: ")
        valid_df = df.loc[
            df["submitter_id"].isin(valid)
        ]  # these are records that weren't successful because they were part of a chunk that failed, but are valid and can be resubmitted without changes
        invalid_df = df.loc[
            df["submitter_id"].isin(invalid)
        ]  # these are records that failed due to being invalid and should be reformatted
        sub_name = ntpath.basename(tsv)
        missing_file = "missing_" + sub_name
        valid_file = "valid_" + sub_name
        invalid_file = "invalid_" + sub_name
        missing_df.to_csv(missing_file, sep="\t", index=False, encoding="utf-8")
        valid_df.to_csv(valid_file, sep="\t", index=False, encoding="utf-8")
        invalid_df.to_csv(invalid_file, sep="\t", index=False, encoding="utf-8")
        print("\t" + missing_file)
        print("\t" + valid_file)
        print("\t" + invalid_file)

    return results


def property_counts_table(prop, df):
    df = df[df[prop].notnull()]
    counts = Counter(df[prop])
    df1 = pd.DataFrame.from_dict(counts, orient="index").reset_index()
    df1 = df1.rename(columns={"index": prop, 0: "count"}).sort_values(
        by="count", ascending=False
    )
    with pd.option_context("display.max_rows", None, "display.max_columns", None):
        display(df1)


def property_counts_by_project(prop, df):
    df = df[df[prop].notnull()]
    categories = list(set(df[prop]))
    projects = list(set(df["project_id"]))

    project_table = pd.DataFrame(columns=["Project", "Total"] + categories)
    project_table

    proj_counts = {}
    for project in projects:
        cat_counts = {}
        cat_counts["Project"] = project
        df1 = df.loc[df["project_id"] == project]
        total = 0
        for category in categories:
            cat_count = len(df1.loc[df1[prop] == category])
            total += cat_count
            cat_counts[category] = cat_count

        cat_counts["Total"] = total
        index = len(project_table)
        for key in list(cat_counts.keys()):
            project_table.loc[index, key] = cat_counts[key]

        project_table = project_table.sort_values(
            by="Total", ascending=False, na_position="first"
        )

    return project_table


def plot_categorical_property(property, df):
    # plot a bar graph of categorical variable counts in a dataframe
    df = df[df[property].notnull()]
    N = len(df)
    categories, counts = zip(*Counter(df[property]).items())
    y_pos = np.arange(len(categories))
    plt.bar(y_pos, counts, align="center", alpha=0.5)
    # plt.figtext(.8, .8, 'N = '+str(N))
    plt.xticks(y_pos, categories)
    plt.ylabel("Counts")
    plt.title(str("Counts by " + property + " (N = " + str(N) + ")"))
    plt.xticks(rotation=90, horizontalalignment="center")
    # add N for each bar
    plt.show()


def plot_numeric_property(property, df, by_project=False):
    # plot a histogram of numeric variable in a dataframe
    df = df[df[property].notnull()]
    data = list(df[property])
    N = len(data)
    fig = sns.distplot(
        data,
        hist=False,
        kde=True,
        bins=int(180 / 5),
        color="darkblue",
        kde_kws={"linewidth": 2},
    )
    plt.figtext(0.8, 0.8, "N = " + str(N))
    plt.xlabel(property)
    plt.ylabel("Probability")
    plt.title(
        "PDF for all projects " + property + " (N = " + str(N) + ")"
    )  # You can comment this line out if you don't need title
    plt.show(fig)

    if by_project is True:
        projects = list(set(df["project_id"]))
        for project in projects:
            proj_df = df[df["project_id"] == project]
            data = list(proj_df[property])
            N = len(data)
            fig = sns.distplot(
                data,
                hist=False,
                kde=True,
                bins=int(180 / 5),
                color="darkblue",
                kde_kws={"linewidth": 2},
            )
            plt.figtext(0.8, 0.8, "N = " + str(N))
            plt.xlabel(property)
            plt.ylabel("Probability")
            plt.title(
                "PDF for " + property + " in " + project + " (N = " + str(N) + ")"
            )  # You can comment this line out if you don't need title
            plt.show(fig)


def submit_file(project_id, filename, chunk_size=30, row_offset=0):
    # Read the file in as a pandas DataFrame
    f = os.path.basename(filename)
    if f.lower().endswith(".csv"):
        df = pd.read_csv(filename, header=0, sep=",", dtype=str).fillna("")
    elif f.lower().endswith(".xlsx"):
        xl = pd.ExcelFile(filename, dtype=str)  # load excel file
        sheet = xl.sheet_names[0]  # sheetname
        df = xl.parse(sheet)  # save sheet as dataframe
        converters = {col: str for col in list(df)}
        df = pd.read_excel(filename, converters=converters).fillna("")
    elif filename.lower().endswith((".tsv", ".txt")):
        df = pd.read_csv(filename, header=0, sep="\t", dtype=str).fillna("")
    else:
        print("Please upload a file in CSV, TSV, or XLSX format.")
        exit()

    # Chunk the file
    print("\nSubmitting " + filename + " with " + str(len(df)) + " records.")
    program, project = project_id.split("-", 1)
    api_url = "{}/api/v0/submission/{}/{}".format(self._endpoint, program, project)
    headers = {"content-type": "text/tab-separated-values"}

    start = row_offset
    end = row_offset + chunk_size
    chunk = df[start:end]

    count = 0

    results = {
        "failed": {"messages": [], "submitter_ids": []},  # these are invalid records
        "other": [],  # any unhandled API responses
        "details": [],  # entire API response details
        "succeeded": [],  # list of submitter_ids that were successfully updated/created
        "responses": [],  # list of API response codes
        "missing": [],
    }  # list of submitter_ids missing from API response details

    while (start + len(chunk)) <= len(df):
        timeout = False
        valid = []
        invalid = []
        count += 1
        print(
            "Chunk "
            + str(count)
            + " (chunk size: "
            + str(chunk_size)
            + ", submitted: "
            + str(len(results["succeeded"]) + len(results["failed"]["submitter_ids"]))
            + " of "
            + str(len(df))
            + ", now submitting rows "
            + str(start)
            + " to "
            + str(end)
            + "):  "
        )

        response = requests.put(
            api_url,
            auth=self._auth_provider,
            data=chunk.to_csv(sep="\t", index=False),
            headers=headers,
        ).text
        results["details"].append(response)

        # Handle the API response
        if '"code": 200' in response:  # success
            res = json.loads(response)
            entities = res["entities"]
            print("\t Succeeded: " + str(len(entities)) + " entities.")
            results["responses"].append(
                "Chunk "
                + str(count)
                + " Succeeded: "
                + str(len(entities))
                + " entities."
            )
            # res = json.loads(response)
            for entity in entities:
                sid = entity["unique_keys"][0]["submitter_id"]
                results["succeeded"].append(sid)

        elif '"code": 4' in response:  # failure
            res = json.loads(response)
            entities = res["entities"]
            print("\tFailed: " + str(len(entities)) + " entities.")
            results["responses"].append(
                "Chunk " + str(count) + " Failed: " + str(len(entities)) + " entities."
            )
            print(res)  # trouble-shooting
            # res = json.loads(response)
            for entity in entities:
                sid = entity["unique_keys"][0]["submitter_id"]
                if entity["valid"]:  # valid but failed
                    valid.append(sid)
                else:  # invalid and failed
                    message = entity["errors"][0]["message"]
                    print(message)  # trouble-shooting
                    results["failed"]["messages"].append(message)
                    results["failed"]["submitter_ids"].append(sid)
                    invalid.append(sid)
            print("\tInvalid records in this chunk: " + str(len(invalid)))

        elif (
            '"error": {"Request Timeout' in response
            or "413 Request Entity Too Large" in response
        ):  # timeout
            print("\t Request Timeout: " + response)
            results["responses"].append("Request Timeout: " + response)
            timeout = True

        elif '"code": 5' in response:  # internal server error
            print("\t Internal Server Error: " + response)
            results["responses"].append("Internal Server Error: " + response)

        elif '"message": ' in response and "code" not in response:  # other?
            print(
                "\t No code in the API response for Chunk "
                + str(count)
                + ": "
                + res["message"]
            )
            print("\t " + str(res["transactional_errors"]))
            results["responses"].append(
                "Error Chunk " + str(count) + ": " + res["message"]
            )
            results["other"].append(res["transactional_errors"])

        else:  # catch-all for any other response
            print("\t Unhandled API-response: " + response)
            results["responses"].append("Unhandled API response: " + response)

        if (
            len(valid) > 0
        ):  # if valid entities failed bc grouped with invalid, retry submission
            chunk = chunk.loc[
                df["submitter_id"].isin(valid)
            ]  # these are records that weren't successful because they were part of a chunk that failed, but are valid and can be resubmitted without changes
            print(
                "Retrying submission of valid entities from failed chunk: "
                + str(len(chunk))
                + " valid entities."
            )

        elif timeout is False:  # get new chunk if didn't timeout
            start += chunk_size
            end = start + chunk_size
            chunk = df[start:end]

        else:  # if timeout, reduce chunk size and retry smaller chunk
            chunk_size = int(chunk_size / 2)
            end = start + chunk_size
            chunk = df[start:end]
            print("Retrying Chunk with reduced chunk_size: " + str(chunk_size))
            timeout = False

    print("Finished data submission.")
    print("Successful records: " + str(len(set(results["succeeded"]))))
    print(
        "Failed invalid records: " + str(len(set(results["failed"]["submitter_ids"])))
    )

    return results


def write_tsvs_from_results(invalid_ids, filename):
    # Read the file in as a pandas DataFrame
    f = os.path.basename(filename)
    if f.lower().endswith(".csv"):
        df = pd.read_csv(filename, header=0, sep=",", dtype=str).fillna("")
    elif f.lower().endswith(".xlsx"):
        xl = pd.ExcelFile(filename, dtype=str)  # load excel file
        sheet = xl.sheet_names[0]  # sheetname
        df = xl.parse(sheet)  # save sheet as dataframe
        converters = {
            col: str for col in list(df)
        }  # make sure int isn't converted to float
        df = pd.read_excel(filename, converters=converters).fillna("")  # remove nan
    elif filename.lower().endswith((".tsv", ".txt")):
        df = pd.read_csv(filename, header=0, sep="\t", dtype=str).fillna("")
    else:
        print("Please upload a file in CSV, TSV, or XLSX format.")
        exit(1)

    invalid_df = df.loc[
        df["submitter_id"].isin(invalid_ids)
    ]  # these are records that failed due to being invalid and should be reformatted
    invalid_file = "invalid_" + f + ".tsv"

    print("Writing TSVs: ")
    print("\t" + invalid_file)
    invalid_df.to_csv(invalid_file, sep="\t", index=False, encoding="utf-8")

    return invalid_df


def paginate_query_json(
    node, project_id=None, props=["id", "submitter_id"], chunk_size=1000
):
    if project_id is not None:
        program, project = project_id.split("-", 1)
        query_txt = """{_%s_count (project_id:"%s")}""" % (node, project_id)
    else:
        query_txt = """{_%s_count}""" % (node)
    # First query the node count to get the expected number of results for the requested query:

    try:
        res = sub.query(query_txt)
        count_name = "_".join(map(str, ["", node, "count"]))
        qsize = res["data"][count_name]
        print("Total of " + str(qsize) + " records in node " + node)
    except:
        print("Query to get _" + node + "_count failed! " + str(res))

    # Now paginate the actual query:
    properties = " ".join(map(str, props))
    offset = 0
    total = {}
    total["data"] = {}
    total["data"][node] = []
    while offset < qsize:
        print(
            "Query Total: "
            + str(qsize)
            + ", Offset: "
            + str(offset)
            + ", Chunk_Size: "
            + str(chunk_size)
        )

        if project_id is not None:
            query_txt = """{%s (first: %s, offset: %s, project_id:"%s"){%s}}""" % (
                node,
                chunk_size,
                offset,
                project_id,
                properties,
            )
        else:
            query_txt = """{%s (first: %s, offset: %s){%s}}""" % (
                node,
                chunk_size,
                offset,
                properties,
            )

        res = sub.query(query_txt)
        if "data" in res:
            total["data"][node] += res["data"][node]
            offset += chunk_size
        elif "error" in res:
            print(res["error"])
            if chunk_size > 1:
                chunk_size = int(chunk_size / 2)
                print("Halving chunk_size to: " + str(chunk_size) + ".")
            else:
                print("Query timing out with chunk_size of 1!")
                exit(1)
        else:
            print("Query Error: " + str(res))

    return total


def get_urls(guids, api):
    # Get URLs for a list of GUIDs
    if isinstance(guids, str):
        guids = [guids]
    if isinstance(guids, list):
        urls = {}
        for guid in guids:
            index_url = "{}/index/{}".format(api, guid)
            output = requests.get(index_url, auth=auth).text
            guid_index = json.loads(output)
            url = guid_index["urls"][0]
            urls[guid] = url
    else:
        print(
            "Please provide one or a list of data file GUIDs: get_urls\(guids=guid_list\)"
        )
    return urls


def get_guids_for_filenames(file_names, api):
    # Get GUIDs for a list of file_names
    if isinstance(file_names, str):
        file_names = [file_names]
    if not isinstance(file_names, list):
        print(
            "Please provide one or a list of data file file_names: get_guid_for_filename\(file_names=file_name_list\)"
        )
    guids = {}
    for file_name in file_names:
        index_url = api + "/index/index/?file_name=" + file_name
        output = requests.get(index_url, auth=auth).text
        index_record = json.loads(output)
        if len(index_record["records"]) > 0:
            guid = index_record["records"][0]["did"]
            guids[file_name] = guid
    return guids


def delete_uploaded_files(guids, api):
    if isinstance(guids, str):
        guids = [guids]
    if isinstance(guids, list):
        for guid in guids:
            fence_url = api + "user/data/" + guid
            response = requests.delete(fence_url, auth=auth)
            if response.status_code == 204:
                print("Successfully deleted GUID {}".format(guid))
            else:
                print("Error deleting GUID {}:".format(guid))
                print(response.reason)


def uploader_index(
    uploader="cgmeyer@uchicago.edu", acl=None, limit=1024, format="guids"
):
    """Submit data in a spreadsheet file containing multiple records in rows to a Gen3 Data Commons.

    Args:
        uploader (str): The uploader's data commons login email.

    Examples:
        This returns all records of files that I uploaded to indexd.
        If I set acl='null', then it will show me my unmapped files,
        including any still in Status 'Generating...'.

        >>> Gen3Submission.submit_file(uploader="cgmeyer@uchicago.edu")
        #data.bloodpac.org/index/index/?limit=1024&acl=null&uploader=cgmeyer@uchicago.edu
    """

    if acl is not None:
        index_url = "{}/index/index/?limit={}&acl={}&uploader={}".format(
            api, limit, acl, uploader
        )
    else:
        index_url = "{}/index/index/?limit={}&uploader={}".format(api, limit, uploader)
    try:
        response = requests.get(index_url, auth=auth).text
    except requests.exceptions.ConnectionError as e:
        print(e)

    try:
        data = json.loads(response)
    except JSONDecodeError as e:
        print(response)
        print(str(e))
        raise Gen3Error("Unable to parse indexd response as JSON!")

    records = data["records"]

    if records is None:
        print(
            "No records in the index for uploader {} with acl {}.".format(uploader, acl)
        )

    elif format is "tsv":
        df = json_normalize(records)
        filename = "indexd_records_for_{}.tsv".format(uploader)
        df.to_csv(filename, sep="\t", index=False, encoding="utf-8")
        return df

    elif format is "guids":
        guids = []
        for record in records:
            guids.append(record["did"])
        return guids

    else:
        return records


## To do
# # get indexd records by uploader:
# /index/index/?acl=null&uploader=cgmeyer@uchicago.edu
#
# api = 'https://vpodc.org/'
# uploader = 'cgmeyer@uchicago.edu'
# index_url = api + '/index/index/?limit=200&acl=null&uploader='+uploader
# output = requests.get(index_url, auth=auth).text
# index_record = json.loads(output)
# index_record
#
# latest=[]
# guids = []
# records = index_record['records']
# for record in records:
#     if '2019-06' in record['updated_date'] or '2019-05-31' in record['updated_date']:
#         print(record)
#         latest.append(record)
#         guids.append(record['did'])
# len(latest)
# len(guids)
#
# # add index search by md5
# https://data.bloodpac.org/index/index/?hash=md5:14c626a4573f2d8e2a1cf796df68a4b8
#
# ## add index stats
# api/index/_stats
#
# ## Add a check authentication command to Gen3sdk:
#
# user_endpoint = api + '/user/user/'
