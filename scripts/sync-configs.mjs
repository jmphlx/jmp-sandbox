import fs from 'node:fs/promises';

export default async function sendGetRequest(authToken) {
  const url = `https://admin.hlx.page/config/jmphlx/sites/jmp-sandbox.json`;

  try {
    const response = await fetch(url, {
      method: 'GET',
      headers: {
        'Authorization': `token ${authToken}`,
        'Accept': '*/*',
        'Content-Type': 'application/json',
      }
    });

    if (!response.ok) return null;
    return await response.json();
  } catch (error) {
    console.error('post request: ', { error });
  }
}

const authToken = process.env.AUTH_TOKEN;
const result = await sendGetRequest(authToken);
await fs.writeFile('config/site.json', JSON.stringify(result), "utf8");
