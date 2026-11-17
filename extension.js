/**
 * tor-egress — estensione pi che registra l'MCP server omonimo.
 *
 * Nessuna dipendenza: avvia `server/server.py` (Python, stdio) con
 * l'interprete indicato da TOR_MCP_PYTHON.
 */
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = dirname(fileURLToPath(import.meta.url));
const python = process.env.TOR_MCP_PYTHON || "python3";

export default function torAnonHttp(pi) {
	pi.registerMcpServer("tor-egress", {
		command: python,
		args: [join(root, "server", "server.py")],
		cwd: root,
		// 6 tool, usati in ogni sessione che fa rete: li vogliamo dichiarati.
		exposure: "direct",
		timeout: 300,
		description:
			"HTTP(S) anonimo via Tor (SOCKS5h + fingerprint da browser): http_request, http_batch, tor_status, tor_exit_ip, tor_newnym. Ogni chiamata di rete del progetto deve passare da qui.",
	});
}
