import fs from 'node:fs/promises';

export default async function sendPostRequest(authToken, yamlText, configType) {
  const url = `https://admin.hlx.page/config/jmphlx/sites/jmp-sandbox/${configType}`;

  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Authorization': `token ${authToken}`,
        'Accept': '*/*',
        'Content-Type': 'text/yaml',
      }, 
      body: yamlText,
    });

    if (!response.ok) return null;
    return response;
  } catch (error) {
    console.error('post request: ', { error });
  }
}

const authToken = process.env.AUTH_TOKEN;
const configPath = process.env.CONFIG_PATH;
const configName = process.env.CONFIG_NAME;

const yamlText = await fs.readFile(configPath, 'utf8');

const result = await sendPostRequest(authToken, yamlText, configName);
console.log(result);
