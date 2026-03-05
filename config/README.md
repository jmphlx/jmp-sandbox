# JMP Sandbox Configurations
The configurations managed in configbus and viewable with https://tools.aem.live/ are stored
in /config. GitHub Actions/Workflows have been created to update or sync certain configuration files; those files live under .github/workflows. 

## Index
The index configuration is stored in **query.yaml**. To push updates to the index file:
1. Make changes to /config/query.yaml.
2. Open a PR in GitHub and have it reviewed.
3. Once the PR is reviewed and merged, go to Actions.
4. From the left rail, select Update Index Configuration File.
5. Above the previously run workflows, select Run workflow. Make sure the Branch is set to main. Then click Run workflow.
6. When the workflow completes successfully, verify using https://tools.aem.live/tools/index-admin/index.html that the corresponding index changes are live.


## Sitemap
The sitemap configuration is stored in **sitemap.yaml**. To push updates to the sitemap file:
1. Make changes to /config/sitemap.yaml.
2. Open a PR in GitHub and have it reviewed.
3. Once the PR is reviewed and merged, go to Actions.
4. From the left rail, select Update Sitemap Configuration File.
5. Above the previously run workflows, select Run workflow. Make sure the Branch is set to main. Then click Run workflow.
6. When the workflow completes successfully, verify using https://tools.aem.live/tools/sitemap-admin/index.html that the corresponding sitemap changes are live.

## Robots
The robots configuration is stored in **robots.txt**. To push updates to the robots file:
1. Make changes to /config/robots.txt.
2. Open a PR in GitHub and have it reviewed.
3. Once the PR is reviewed and merged, go to Actions.
4. From the left rail, select Update Robots.txt Configuration File.
5. Above the previously run workflows, select Run workflow. Make sure the Branch is set to main. Then click Run workflow.
6. When the workflow completes successfully, verify using https://tools.aem.live/tools/robots-edit/index.html that the corresponding robots changes are live.

## Site.json
The version of our site configuration is stored **/config/site.json**. However, we do not push updates to this file. This is to keep a record of the site before making major configuration changes.  To update the site.json file to reflect the latest version of the site:
1. Navigate to GitHub.
2. Select Actions.
3. From the left rail, select Sync site.json file.
4. Above the previously run workflows, select Run workflow. Make sure the Branch is set to main. Then click Run workflow.
5. When the workflow completes, if there were differences between what was in site.json and the current version of the site, you should see a new commit in the history updating site.json.
