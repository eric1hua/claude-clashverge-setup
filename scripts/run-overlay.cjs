// Local validation helper; Clash Verge itself executes the deployed main().
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const context = vm.createContext({ config: input.config, profileName: input.profileName });
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context, { timeout: 2000 });
vm.runInContext('result = main(config, profileName)', context, { timeout: 2000 });
process.stdout.write(JSON.stringify(context.result));
