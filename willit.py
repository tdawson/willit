#!/usr/bin/python3
import bugzilla
import concurrent.futures
import datetime
import dnf
import glob
import json
import os
import pprint
import shutil

from jinja2 import Template
from pathlib import Path

## Variables
installroot = "/installroot"
# Colors
color_good = "#00ff00"
color_bad = "#ff0000"
color_not = "#d9ccd3"
bz_page_size = 20
this_day = datetime.datetime.now().strftime('%Y-%m-%d')
# Lists/Dicts
mainDict = {}
mainList = []

# DNF Bases
def get_base(style, repo_info):
  this_base = dnf.Base()
  conf = this_base.conf
  conf.cachedir = "/var/tmp/willit-dnf-cache-" + repo_info['RepoName']
  conf.installroot = installroot
  if style == "main":
    this_base.repos.add_new_repo(repo_info['RepoName'], conf, baseurl=[repo_info['RepoURL']])
  if style == "next":
    this_base.repos.add_new_repo(repo_info['RepoName'], conf, baseurl=[repo_info['RepoURL']])
    this_base.repos.add_new_repo(repo_info['RepoName'] + "core", conf, baseurl=[repo_info['CoreRepoURL']])
  elif style == "testing":
    this_base.repos.add_new_repo(repo_info['RepoName'] + "testing", conf, baseurl=[repo_info['TestRepoURL']])
  elif style == "main-all":
    this_base.repos.add_new_repo(repo_info['RepoName'], conf, baseurl=[repo_info['RepoURL']])
    for other_repo in repo_info['OtherRepos']:
      this_base.repos.add_new_repo(other_repo['OtherRepoName'], conf, baseurl=[other_repo['OtherRepoURL']])
  elif style == "testing-all":
    this_base.repos.add_new_repo(repo_info['RepoName'], conf, baseurl=[repo_info['RepoURL']])
    this_base.repos.add_new_repo(repo_info['RepoName'] + "testing", conf, baseurl=[repo_info['TestRepoURL']])
    for other_repo in repo_info['OtherRepos']:
      this_base.repos.add_new_repo(other_repo['OtherRepoName'], conf, baseurl=[other_repo['OtherRepoURL']])
  return this_base

# Will the pkg install using the corresponding repos
def will_pkg_install(pkg, style, repo_info):
  this_status = {"status": "pass", "error": ""}
  with dnf.Base() as base:
    conf = base.conf
    conf.cachedir = "/var/tmp/willit-dnf-cache-" + repo_info['RepoName']
    conf.installroot = installroot
    if style == "main":
      base.repos.add_new_repo(repo_info['RepoName'], conf, baseurl=[repo_info['RepoURL']])
      for other_repo in repo_info['OtherRepos']:
        base.repos.add_new_repo(other_repo['OtherRepoName'], conf, baseurl=[other_repo['OtherRepoURL']])
    elif style == "testing":
      base.repos.add_new_repo(repo_info['RepoName'], conf, baseurl=[repo_info['RepoURL']])
      base.repos.add_new_repo(repo_info['RepoName'] + "testing", conf, baseurl=[repo_info['TestRepoURL']])
      for other_repo in repo_info['OtherRepos']:
        base.repos.add_new_repo(other_repo['OtherRepoName'], conf, baseurl=[other_repo['OtherRepoURL']])
    base.fill_sack(load_system_repo=False)
    try:
      base.install(pkg)
      base.resolve()
    except dnf.exceptions.PackageNotFoundError as e:
      this_status['status'] = "fail"
      this_status['error'] = str(e)
    except dnf.exceptions.DepsolveError as e:
      this_status['status'] = "fail"
      this_status['error'] = str(e)
  return this_status

# We need to iterate to get all the bugzilla bugs
def _iterate_bugzilla_query(querydata):
    """Iterate Bugzilla query until all results are fetched."""
    print("    Getting next page of bugz")
    results = bzapi.query(querydata)
    if len(results) == bz_page_size:
        last_result_id = results[-1].id
        querydata['f1'] = 'bug_id'
        querydata['o1'] = 'greaterthan'
        querydata['v1'] = last_result_id
        results += _iterate_bugzilla_query(querydata)
    return results

with open('willit-config.json') as json_file:
  input_config = json.load(json_file)

## Repo Section
for this_repo in input_config['repos']:
  #print('RepoName: ' + this_repo['RepoName'])
  #print('RepoURL: ' + this_repo['RepoURL'])
  #print('CheckTest: ' + this_repo['CheckTest'])
  #print('CheckInstall: ' + this_repo['CheckInstall'])
  #print('CheckBuild: ' + this_repo['CheckBuild'])
  #print('TestRepoURL: ' + this_repo['TestRepoURL'])
  #print('OtherRepos: ' + str(this_repo['OtherRepos']))
  print("")
  print("Working On: " + this_repo['RepoName'])
  if this_repo['RepoName']=="epel9-next" or this_repo['RepoName']=="epel9":
    version = "epel9"
  elif this_repo['RepoName']=="epel8-next" or this_repo['RepoName']=="epel8":
    version = "epel8"
  elif this_repo['RepoName']=="epel7":
    version = "epel7"
  else:
    version = None
    print("    Repo Name not in list, not checking bugz even if it is set")
    this_overall["test_bugz"] = "False"
  try:
    with open('output/' + this_repo['RepoName'] + '/status-repo.json', 'r') as jsonfile:
        old_repo = json.load(jsonfile)
  except IOError:
    old_repo = {}
  try:
    with open('output/' + version + '/status-repo.json', 'r') as jsonfile:
        old_core_repo = json.load(jsonfile)
  except IOError:
    old_core_repo = {}
  this_overall = {}
  this_spkg_list = {}
  core_spkg_list = {}
  this_bugz_no_source = []
  this_bugz_cve = []
  ci_bad_binary = []
  ci_core_bad_binary = []
  cb_bad_builds = []
  test_this_spkg_list = {}
  test_ci_bad_binary = []
  test_cb_bad_builds = []
  this_overall["reponame"] = this_repo['RepoName']
  this_overall["version"] = version
  try:
    this_overall["day"] = old_repo["day"]
  except KeyError:
    this_overall["day"] = this_day
  shutil.rmtree("/var/tmp/willit-dnf-cache-" + this_repo['RepoName'], ignore_errors=True)

  ## Gather a list of all binary packages in main repo.
  print("  Gathering binary packages in repo ... ", end='')
  with dnf.Base() as base:
    conf = base.conf
    conf.cachedir = "/var/tmp/willit-dnf-cache-" + this_repo['RepoName']
    base.repos.add_new_repo(this_repo['RepoName'], conf, baseurl=[this_repo['RepoURL']])
    base.fill_sack(load_system_repo=False)
    query = base.sack.query().available().latest()
    this_bpkg_list = query.run()
  print(len(this_bpkg_list))
  
  ## Get the source rpms out of the binary package list
  print("  Generating source package list ... ", end='')
  for bpkg in this_bpkg_list:
    binarynvr = bpkg.name + "-" + bpkg.evr
    sourcenvr = bpkg.sourcerpm.rsplit(".",2)[0]
    sourcename = sourcenvr.rsplit("-",2)[0]
    this_binary = {}
    this_binary['bname'] = bpkg.name
    this_binary['bnvr'] =  binarynvr
    if sourcename in this_spkg_list:
      this_spkg_list[sourcename]['binaries'].append(this_binary)
    else:
      this_source = {}
      this_source['sname'] = sourcename
      try:
        this_source['sname_day'] = old_repo["spkg_list"][sourcename]['sname_day']
      except KeyError:
        this_source['sname_day'] = this_day
      this_source['snvr'] = sourcenvr
      try:
        this_source['snvr_day'] = old_repo["spkg_list"][sourcename]['snvr_day']
      except KeyError:
        this_source['snvr_day'] = this_day
      this_source['binaries'] = [this_binary]
      this_source['bad_installs'] = {}
      this_source['bad_build'] = []
      this_source['bugz'] = []
      this_source['bug_count'] = 0
      this_source['bug_count_cve'] = 0
      this_spkg_list[sourcename] = this_source
  print(len(this_spkg_list))
    
  this_overall["bnumber"] = len(this_bpkg_list)
  this_overall["snumber"] = len(this_spkg_list)

  # Check and add open bugzilla bugs
  if this_repo['CheckBugz'] == "True":
    this_overall["test_bugz"] = "True"
    print("  Starting Bug Checking")
    
    product="Fedora EPEL"
    
    if version:
      URL = "bugzilla.redhat.com"
      bzapi = bugzilla.Bugzilla(URL)
      bquery = bzapi.build_query(
        product=product,
        version=version,
        status='__open__')
      bquery["limit"] = bz_page_size
      bquery["offset"] = 0
      bquery["order"] = "bug_id"
      bugz = []
      bugz = _iterate_bugzilla_query(bquery)
      for bug in bugz:
        print("    Bug: {0} {1} {2} {3}".format(bug.id, bug.component, bug.status, bug.summary))
        this_bug = {}
        this_bug['id'] = bug.id
        this_bug['status'] = bug.status
        this_bug['summary'] = bug.summary
        this_bug['severity'] = bug.severity
        if bug.component in this_spkg_list:
          this_spkg_list[bug.component]['bugz'].append(this_bug)
          this_spkg_list[bug.component]['bug_count'] += 1
          if 'CVE' in bug.summary:
            this_bugz_cve.append(this_bug)
            this_spkg_list[bug.component]['bug_count_cve'] += 1
          print("      Added")
        else:
          this_bugz_no_source.append(this_bug)
      print("Number of Bugs: {}".format(len(bugz)))
      print("Number of No Source Bugs: {}".format(len(this_bugz_no_source)))
      print("Number of CVE Bugs: {}".format(len(this_bugz_cve)))
      this_overall["bugz_total"] = len(bugz)
      this_overall["bugz_total_no_source"] = len(this_bugz_no_source)
      this_overall["bugz_total_cve"] = len(this_bugz_cve)
      this_overall["bugz_no_source"] = this_bugz_no_source
      this_overall["bugz_cve"] = this_bugz_cve
  else:
    this_overall["test_bugz"] = "False"
  

  # Will It Duplicate
  if this_repo['CheckDuplicates'] == "True":
    this_overall["test_duplicates"] = "True"
    print("  Starting CheckDuplicates")
    
    print("    Gathering Upstream Source Names")
    upstream_source_names = []
    duplicate_list = []
    ## Gather a list of all binary packages in upstream repos.
    print("      Gathering upstream binary packages ... ", end='')
    with dnf.Base() as base:
      conf = base.conf
      conf.cachedir = "/var/tmp/willit-dnf-cache-" + this_repo['RepoName']
      for other_repo in this_repo['OtherRepos']:
        base.repos.add_new_repo(other_repo['OtherRepoName'], conf, baseurl=[other_repo['OtherRepoURL']])
      base.fill_sack(load_system_repo=False)
      query = base.sack.query().available().latest()
      upstream_bpkg_list = query.run()
      print(len(upstream_bpkg_list))
      ## Get the source rpms out of the binary package list
      print("      Gathering upstream source package names ... ", end='')
      for bpkg in upstream_bpkg_list:
        if bpkg.sourcerpm:
          sourcenvr = bpkg.sourcerpm.rsplit(".",2)[0]
          sourcename = sourcenvr.rsplit("-",2)[0]
        else:
          sourcename = bpkg.name
        if sourcename not in upstream_source_names:
          upstream_source_names.append(sourcename)
      print(len(upstream_source_names))
      
    print("    Checking our souce names against upstreams")
    for spkg in this_spkg_list.keys():
      if spkg in upstream_source_names:
        print("      {} - Duplicate".format(spkg))
        duplicate_list.append(spkg)
    this_overall["duplicate_list"] = duplicate_list
    this_overall["duplicate_total"] = len(duplicate_list)
  else:
    this_overall["test_duplicates"] = "False"
    this_overall["duplicate_list"] = []
    this_overall["duplicate_total"] = 0


  # Will It Install
  if this_repo['CheckInstall'] == "True":
    this_overall["test_install"] = "True"
    print("  Starting CheckInstall")
    for bpkg in this_bpkg_list:
      #print(".", end='')
      with concurrent.futures.ProcessPoolExecutor(max_workers=1) as executor:
        bpkg_status = executor.submit(will_pkg_install, bpkg.name, "main", this_repo).result()
      #bpkg_status = will_pkg_install(bpkg.name, "main", this_repo)
      if bpkg_status['status'] == "fail":
        binarynvr = bpkg.name + "-" + bpkg.evr
        sourcenvr = bpkg.sourcerpm.rsplit(".",2)[0]
        sourcename = sourcenvr.rsplit("-",2)[0]
        bbinary = {}
        bbinary['bname'] = bpkg.name
        bbinary['bnvr'] = binarynvr
        bbinary['sname'] = sourcename
        try:
          bbinary['day'] = old_repo["spkg_list"][sourcename]['bad_installs'][binarynvr]['day']
        except KeyError:
          bbinary['day'] = this_day
        bbinary['error'] = bpkg_status['error']
        ci_bad_binary.append(bbinary)
        this_spkg_list[sourcename]['bad_installs'][binarynvr] = bbinary
        print("    Wont Install: " + binarynvr)

    print("      Failed Installs: " + str(len(ci_bad_binary)))
    this_overall["ci_bnumber_good"] = this_overall["bnumber"] - len(ci_bad_binary)
    this_overall["ci_bnumber_bad"] = len(ci_bad_binary)
    if len(ci_bad_binary) > 0:
      this_overall["ci_bcolor"] = color_bad
    else:
      this_overall["ci_bcolor"] = color_good

    # If we are a next repo
    #  check if any core packages fail to install on next
    if this_repo['IsNext'] == "True":
      this_overall["IsNext"] = "True"
      print("  Starting CheckInstall of Core Packages")
      for spkg, sinfo in old_core_repo["spkg_list"].items():
        next_spkg_list = this_spkg_list.keys()
        if spkg not in next_spkg_list:
          for bpkg in sinfo['binaries']:
            with concurrent.futures.ProcessPoolExecutor(max_workers=1) as executor:
              bpkg_status = executor.submit(will_pkg_install, bpkg['bname'], "main", this_repo).result()
            #bpkg_status = will_pkg_install(bpkg.name, "main", this_repo)
            if bpkg_status['status'] == "fail":
              binarynvr = bpkg['bnvr']
              sourcenvr = sinfo['snvr']
              sourcename = spkg
              print("    Wont Install: " + binarynvr)
              bbinary = {}
              bbinary['bname'] = bpkg['bname']
              bbinary['bnvr'] = binarynvr
              bbinary['sname'] = sourcename
              bbinary['error'] = bpkg_status['error']
              try:
                bbinary['day'] = old_core_repo["spkg_list"][sourcename]['bad_installs'][binarynvr]['day']
                print("      Fails on " + version)
              except KeyError:
                try:
                  bbinary['day'] = old_repo["core"][sourcename]['bad_installs'][binarynvr]['day']
                except KeyError:
                  bbinary['day'] = this_day
                ci_core_bad_binary.append(bbinary)
                if sourcename in core_spkg_list:
                  core_spkg_list[sourcename]['bad_installs'][binarynvr] = bbinary
                else:
                  this_source = {}
                  this_source['bad_installs'] = {}
                  this_source['sname'] = sourcename
                  this_source['snvr'] = sourcenvr
                  this_source['binaries'] = old_core_repo["spkg_list"][sourcename]['binaries']
                  this_source['bad_installs'][binarynvr] = bbinary
                  core_spkg_list[sourcename] = this_source

      print("      Failed Core Installs: " + str(len(ci_core_bad_binary)))
      this_overall["core"] = core_spkg_list
      this_overall["ci_core_bnumber_good"] = old_core_repo["bnumber"] - len(ci_bad_binary) - len(ci_core_bad_binary)
      this_overall["ci_core_bnumber_bad"] = len(ci_core_bad_binary)
      if len(ci_core_bad_binary) > 0:
        this_overall["ci_core_bcolor"] = color_bad
      else:
        this_overall["ci_core_bcolor"] = color_good
    else:
      this_overall["IsNext"] = "False"
      this_overall["ci_core_bnumber_good"] = "--"
      this_overall["ci_core_bnumber_bad"] = "--"
      this_overall["ci_core_bcolor"] = color_not
  else:
    this_overall["test_install"] = "False"
    this_overall["IsNext"] = "False"
    this_overall["ci_bnumber_good"] = "--"
    this_overall["ci_bnumber_bad"] = "--"
    this_overall["ci_bcolor"] = color_not
    this_overall["ci_core_bnumber_good"] = "--"
    this_overall["ci_core_bnumber_bad"] = "--"
    this_overall["ci_core_bcolor"] = color_not

  # Will It Build
  if this_repo['CheckBuild'] == "True":
    this_overall["test_build"] = "True"
    print("  Starting CheckBuild")
    this_overall["cb_snumber_good"] = this_overall["snumber"]
    this_overall["cb_snumber_bad"] = 0
    this_overall["cb_scolor"] = color_good
  else:
    this_overall["test_build"] = "False"
    this_overall["cb_snumber_good"] = "--"
    this_overall["cb_snumber_bad"] = "--"
    this_overall["cb_scolor"] = color_not
  
  # Work on Testing if we need to
  if this_repo['CheckTest'] == "True":
    this_overall["test_checked"] = "True"
    ## Gather a list of all binary packages in testing repo.
    print("  Gathering binary packages in testing repo ... ", end='')
    with dnf.Base() as base:
      conf = base.conf
      conf.cachedir = "/var/tmp/willit-dnf-cache-" + this_repo['RepoName']
      base.repos.add_new_repo(this_repo['RepoName'] + "testing", conf, baseurl=[this_repo['TestRepoURL']])
      base.fill_sack(load_system_repo=False)
      query = base.sack.query().available().latest()
      this_bpkg_list = query.run()
    print(len(this_bpkg_list))

    ## Set the source rpms out of the binary package list
    print("  Generating testing source package list ... ", end='')
    for bpkg in this_bpkg_list:
      binarynvr = bpkg.name+"-"+bpkg.evr
      sourcenvr = bpkg.sourcerpm.rsplit(".",2)[0]
      sourcename = sourcenvr.rsplit("-",2)[0]
      this_binary = {}
      this_binary['bname'] = bpkg.name
      this_binary['bnvr'] =  binarynvr   
      if sourcename in test_this_spkg_list:
        test_this_spkg_list[sourcename]['binaries'].append(this_binary)
      else:
        this_source = {}
        this_source['sname'] = sourcename
        this_source['snvr'] = sourcenvr
        this_source['binaries'] = [this_binary]
        this_source['bad_installs'] = {}
        this_source['bad_build'] = []
        this_source['bugz'] = []
        this_source['bug_count'] = 0
        this_source['bug_count_cve'] = 0
        test_this_spkg_list[sourcename] = this_source
    print(len(test_this_spkg_list))

    this_overall["test_bnumber"] = len(this_bpkg_list)
    this_overall["test_snumber"] = len(test_this_spkg_list)

    # Will It Install - For Testing
    if this_repo['CheckInstall'] == "True":
      print("  Starting CheckInstall")
      for bpkg in this_bpkg_list:
        #print(".", end='')
        with concurrent.futures.ProcessPoolExecutor(max_workers=1) as executor:
          bpkg_status = executor.submit(will_pkg_install, bpkg.name, "testing", this_repo).result()
        #bpkg_status = will_pkg_install(bpkg.name, "testing", this_repo)
        if bpkg_status['status'] == "fail":
          binarynvr = bpkg.name + "-" + bpkg.evr
          sourcenvr = bpkg.sourcerpm.rsplit(".",2)[0]
          sourcename = sourcenvr.rsplit("-",2)[0]
          bbinary = {}
          bbinary['bname'] = bpkg.name
          bbinary['bnvr'] = binarynvr
          bbinary['sname'] = sourcename
          bbinary['error'] = bpkg_status['error']
          test_ci_bad_binary.append(bbinary)
          test_this_spkg_list[sourcename]['bad_installs'][binarynvr] = bbinary
          print("    Wont Install: " + binarynvr)
          
      print("      Failed Installs: " + str(len(test_ci_bad_binary)))
      this_overall["test_ci_bnumber_good"] = this_overall["test_bnumber"] - len(test_ci_bad_binary)
      this_overall["test_ci_bnumber_bad"] = len(test_ci_bad_binary)
      if len(test_ci_bad_binary) > 0:
        this_overall["test_ci_bcolor"] = color_bad
      else:
        this_overall["test_ci_bcolor"] = color_good
    else:
      this_overall["test_ci_bnumber_good"] = "--"
      this_overall["test_ci_bnumber_bad"] = "--"
      this_overall["test_ci_bcolor"] = color_not

    # Will It Build - For Testing
    if this_repo['CheckBuild'] == "True":
      print("  Starting CheckBuild")
      this_overall["test_cb_snumber_good"] = this_overall["test_snumber"]
      this_overall["test_cb_snumber_bad"] = 2
      this_overall["test_cb_scolor"] = color_bad
    else:
      this_overall["test_cb_snumber_good"] = "--"
      this_overall["test_cb_snumber_bad"] = "--"
      this_overall["test_cb_scolor"] = color_not
  else:
    this_overall["test_checked"] = "FALSE"
    this_overall["test_bnumber"] = "--"
    this_overall["test_snumber"] = "--"
    this_overall["test_ci_bnumber_good"] = "--"
    this_overall["test_ci_bnumber_bad"] = "--"
    this_overall["test_ci_bcolor"] = color_not
    this_overall["test_cb_snumber_good"] = "--"
    this_overall["test_cb_snumber_bad"] = "--"
    this_overall["test_cb_scolor"] = color_not
  
  # Work with data
  this_overall["spkg_list"] = this_spkg_list
  this_overall["test_spkg_list"] = test_this_spkg_list
  mainList.append(this_overall)
  Path("output/" + this_repo['RepoName'] + "/packages").mkdir(parents=True, exist_ok=True)
  Path("output/" + this_repo['RepoName'] + "/testing-packages").mkdir(parents=True, exist_ok=True)
  for pf in glob.glob("output/" + this_repo['RepoName'] + "/*packages/*.html"):
    os.remove(pf)
  with open('templates/status-wont-install.html.jira') as f:
    witmpl = Template(f.read())
  with open('output/' + this_repo['RepoName'] + '/status-wont-install.html', 'w') as w:
    w.write(witmpl.render(
      this_date=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
      badInstall=ci_bad_binary,
      badInstallNum=len(ci_bad_binary),
      testBadInstall=test_ci_bad_binary,
      testBadInstallNum=len(test_ci_bad_binary),
      repo=this_overall))
  if this_repo['IsNext'] == "True" and this_repo['CheckInstall'] == "True":
    with open('templates/status-core-wont-install.html.jira') as f:
      wictmpl = Template(f.read())
    with open('output/' + this_repo['RepoName'] + '/status-core-wont-install.html', 'w') as w:
      w.write(wictmpl.render(
        this_date=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        badInstall=ci_core_bad_binary,
        badInstallNum=len(ci_core_bad_binary),
        version=version,
        repo=this_overall))
    with open('templates/status-core-package.html.jira') as f:
      pctmpl = Template(f.read())
    for spkg in core_spkg_list.values() :
      with open('output/' + this_repo['RepoName'] + '/packages/' + spkg['sname'] + '.html', 'w') as w:
        w.write(pctmpl.render(
          this_date=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
          repoName=this_repo['RepoName'],
          version=version,
          spkg=spkg))
  if this_overall["test_duplicates"] == "True":
    with open('templates/status-duplicates.html.jira') as f:
      bnstmpl = Template(f.read())
    with open('output/' + this_repo['RepoName'] + '/status-duplicates.html', 'w') as w:
      w.write(bnstmpl.render(
        this_date=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        repo=this_overall))
  if this_overall["test_bugz"] == "True":
    with open('templates/status-bugz-no-source.html.jira') as f:
      bnstmpl = Template(f.read())
    with open('output/' + this_repo['RepoName'] + '/status-bugz-no-source.html', 'w') as w:
      w.write(bnstmpl.render(
        this_date=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        repo=this_overall))
    with open('templates/status-bugz-cve.html.jira') as f:
      bcvetmpl = Template(f.read())
    with open('output/' + this_repo['RepoName'] + '/status-bugz-cve.html', 'w') as w:
      w.write(bcvetmpl.render(
        this_date=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        repo=this_overall))
  with open('output/' + this_repo['RepoName'] + '/status-repo.json', 'w') as w:
    json.dump(this_overall, w)
#  with open('output/' + this_repo['RepoName'] + '/status-packages.json', 'w') as w:
#    json.dump(this_spkg_list, w)
  with open('templates/status-repo.html.jira') as f:
    tmpl = Template(f.read())
  with open('output/' + this_repo['RepoName'] + '/status-repo.html', 'w') as w:
    w.write(tmpl.render(
      this_date=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
      badInstall=ci_bad_binary,
      badInstallNum=len(ci_bad_binary),
      badBuild=cb_bad_builds,
      badBuildNum=len(cb_bad_builds),
      testBadInstall=test_ci_bad_binary,
      testBadInstallNum=len(test_ci_bad_binary),
      testBadBuild=test_cb_bad_builds,
      testBadBuildNum=len(test_cb_bad_builds),
      thisNum=len(this_spkg_list),
      thisTestNum=len(test_this_spkg_list),
      repo=this_overall))
  with open('templates/index-package.html.jira') as f:
    iptmpl = Template(f.read())
  with open('output/' + this_repo['RepoName'] + '/index-packages.html', 'w') as w:
    w.write(iptmpl.render(
      this_date=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
      repoName=this_repo['RepoName'],
      pkgDir="packages",
      repo=this_overall,
      spkgList=this_spkg_list.values()))
  if this_repo['CheckTest'] == "True":
#    with open('output/' + this_repo['RepoName'] + '/status-test-packages.json', 'w') as w:
#      json.dump(test_this_spkg_list, w)
    with open('output/' + this_repo['RepoName'] + '/index-test-packages.html', 'w') as w:
      w.write(iptmpl.render(
        this_date=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        repoName=this_repo['RepoName'] + "testing",
        pkgDir="testing-packages",
        spkgList=test_this_spkg_list.values()))
  with open('templates/status-package.html.jira') as f:
    ptmpl = Template(f.read())
  for spkg in this_spkg_list.values() :
    with open('output/' + this_repo['RepoName'] + '/packages/' + spkg['sname'] + '.html', 'w') as w:
      w.write(ptmpl.render(
        this_date=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        repoName=this_repo['RepoName'],
        spkg=spkg))
  if this_repo['CheckTest'] == "True":
    for spkg in test_this_spkg_list.values() :
      with open('output/' + this_repo['RepoName'] + '/testing-packages/' + spkg['sname'] + '.html', 'w') as w:
        w.write(ptmpl.render(
          this_date=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
          repoName=this_repo['RepoName'],
          spkg=spkg))


## Overall Section
Path("output").mkdir(parents=True, exist_ok=True)

# Write out Overall json file
with open('output/status-overall.json', 'w') as file:
    json.dump(mainList, file)

# Write out Overall Status Page
with open('templates/status-overall.html.jira') as f:
  tmpl = Template(f.read())
with open('output/status-overall.html', 'w') as w:
  w.write(tmpl.render(
    this_date=datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
    color_good=color_good,
    color_bad=color_bad,
    color_not=color_not,
    repos=mainList))
