const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  
  try {
    console.log("Navigating to http://127.0.0.1:5173...");
    await page.goto('http://127.0.0.1:5173', { waitUntil: 'networkidle', timeout: 10000 });
    
    // Wait a bit extra to ensure dynamic elements like Cytoscape finish rendering
    await page.waitForTimeout(2000); 

    console.log("Taking screenshot...");
    await page.screenshot({ path: 'screenshot.png', fullPage: true });
    console.log("Screenshot saved to screenshot.png");
  } catch (error) {
    console.error("Error capturing screenshot:", error);
  } finally {
    await browser.close();
  }
})();