"use strict";

/*
 * weston-rail-web – Browser-Seite.
 *
 * Anmeldung und Programmliste holt die Seite vom eigenen Dienst; die
 * laufende Sitzung spricht über eine WebSocket das Guacamole-Protokoll,
 * dargestellt von guacamole-common-js (Apache-2.0).
 */

const $ = (id) => document.getElementById(id);

const state = {
	user: null,
	sessions: new Map(),   /* app -> { client, element, tab, display } */
	active: null,
};

/* ------------------------------------------------------------ Anmeldung */

async function api(path, options) {
	const response = await fetch(path, Object.assign({
		headers: { "Content-Type": "application/json" },
		credentials: "same-origin",
	}, options || {}));
	let body = {};
	try { body = await response.json(); } catch (e) { /* leer */ }
	return { ok: response.ok, status: response.status, body };
}

function showLogin(message) {
	$("app-view").classList.add("hidden");
	$("login-view").classList.remove("hidden");
	const error = $("login-error");
	if (message) {
		error.textContent = message;
		error.classList.remove("hidden");
	} else {
		error.classList.add("hidden");
	}
	$("login-pass").value = "";
	updateLoginButton();
	$("login-user").focus();
}

function updateLoginButton() {
	$("login-button").disabled = !($("login-user").value && $("login-pass").value);
}

async function login(event) {
	event.preventDefault();
	const user = $("login-user").value.trim();
	const password = $("login-pass").value;
	$("login-button").disabled = true;
	const result = await api("/api/login", {
		method: "POST",
		body: JSON.stringify({ user: user, password: password }),
	});
	if (!result.ok) {
		showLogin(result.body.error || "Anmeldung fehlgeschlagen");
		return;
	}
	state.user = result.body.user;
	$("login-pass").value = "";
	await showApps();
}

async function logout() {
	for (const app of Array.from(state.sessions.keys()))
		closeSession(app);
	await api("/api/logout", { method: "POST" });
	state.user = null;
	hidePopups();
	showLogin(null);
}

/* ------------------------------------------------------------ Programme */

function initials(name) {
	const clean = (name || "?").replace(/^.*\\/, "");
	return clean.charAt(0);
}

async function showApps() {
	const result = await api("/api/apps");
	if (result.status === 401) {
		showLogin(null);
		return;
	}
	$("login-view").classList.add("hidden");
	$("app-view").classList.remove("hidden");
	$("avatar").textContent = initials(state.user);
	$("account-avatar").textContent = initials(state.user);
	$("account-user").textContent = state.user;
	$("account-sub").textContent = state.user;
	$("workspace-name").textContent = result.body.workspace || "Work Resources";

	const container = $("apps");
	container.textContent = "";
	(result.body.apps || []).forEach((app) => {
		const tile = document.createElement("button");
		tile.className = "app";
		tile.title = app.title;
		tile.onclick = () => startSession(app);

		const icon = document.createElement("img");
		icon.src = "/api/icon/" + encodeURIComponent(app.name);
		icon.alt = "";
		const name = document.createElement("span");
		name.className = "name";
		name.textContent = app.title;

		tile.appendChild(icon);
		tile.appendChild(name);
		container.appendChild(tile);
	});
	$("apps-empty").classList.toggle("hidden", (result.body.apps || []).length > 0);
	showHome();
}

/* -------------------------------------------------------------- Sitzung */

function showHome() {
	state.active = null;
	$("home").classList.remove("hidden");
	for (const session of state.sessions.values()) {
		session.element.classList.add("hidden");
		session.tab.classList.remove("active");
	}
	$("home-tab").classList.add("active");
}

function showSession(app) {
	const session = state.sessions.get(app);
	if (!session)
		return;
	state.active = app;
	$("home").classList.add("hidden");
	$("home-tab").classList.remove("active");
	for (const [name, other] of state.sessions) {
		const visible = name === app;
		other.element.classList.toggle("hidden", !visible);
		other.tab.classList.toggle("active", visible);
	}
	resize(session);
	session.client.getDisplay().getElement().focus();
}

function dialog(title, text, show) {
	$("connect-title").textContent = title;
	$("connect-state").textContent = text;
	$("connect-dialog").classList.toggle("hidden", !show);
	if (show)
		$("connect-log").classList.add("hidden");
}

function logLine(text) {
	const log = $("connect-log");
	log.textContent += text + "\n";
}

function resize(session) {
	const element = session.element;
	const display = session.client.getDisplay();
	const width = Math.max(640, element.clientWidth);
	const height = Math.max(480, element.clientHeight);
	if (session.width === width && session.height === height)
		return;
	session.width = width;
	session.height = height;
	session.client.sendSize(width, height);
	display.scale(1);
}

function startSession(app) {
	if (state.sessions.has(app.name)) {
		showSession(app.name);
		return;
	}

	dialog(app.title + " wird verbunden und gestartet.",
	       "Sichere Verbindung wird hergestellt…", true);

	const element = document.createElement("div");
	element.className = "session hidden";
	$("sessions").appendChild(element);

	const width = Math.max(640, window.innerWidth);
	const height = Math.max(480, window.innerHeight - 48);
	const parameters = "app=" + encodeURIComponent(app.name) +
		"&width=" + width + "&height=" + height +
		"&dpi=" + Math.round(96 * (window.devicePixelRatio || 1));
	const url = (location.protocol === "https:" ? "wss://" : "ws://") +
		location.host + "/ws?" + parameters;

	const tunnel = new Guacamole.WebSocketTunnel(url);
	const client = new Guacamole.Client(tunnel);
	const display = client.getDisplay();
	const displayElement = display.getElement();
	displayElement.tabIndex = 0;
	element.appendChild(displayElement);

	const session = { client: client, element: element, width: 0, height: 0 };

	/* Tab in der Kopfleiste */
	const tab = document.createElement("div");
	tab.className = "tab";
	const tabIcon = document.createElement("img");
	tabIcon.src = "/api/icon/" + encodeURIComponent(app.name);
	tabIcon.alt = "";
	const tabTitle = document.createElement("span");
	tabTitle.className = "title";
	tabTitle.textContent = app.title;
	const tabClose = document.createElement("button");
	tabClose.className = "close";
	tabClose.textContent = "✕";
	tabClose.title = "Schließen";
	tabClose.onclick = (event) => { event.stopPropagation(); closeSession(app.name); };
	tab.appendChild(tabIcon);
	tab.appendChild(tabTitle);
	tab.appendChild(tabClose);
	tab.onclick = () => showSession(app.name);
	$("tabs").appendChild(tab);
	session.tab = tab;

	state.sessions.set(app.name, session);

	client.onstatechange = (code) => {
		/* 3 = verbunden */
		if (code === 3) {
			dialog("", "", false);
			showSession(app.name);
		} else if (code === 5) {
			closeSession(app.name);
		}
	};
	client.onerror = (status) => {
		logLine("Fehler: " + (status && status.message ? status.message : status));
		dialog(app.title + " konnte nicht gestartet werden.",
		       (status && status.message) || "Verbindung fehlgeschlagen", true);
		$("connect-cancel").textContent = "Schließen";
		cleanupSession(app.name);
	};
	tunnel.onerror = (status) => {
		logLine("Verbindungsfehler: " +
			(status && status.message ? status.message : JSON.stringify(status)));
	};

	dialog(app.title + " wird verbunden und gestartet.",
	       "Remoteverbindung wird konfiguriert…", true);
	client.connect("");

	/* Eingaben */
	const mouse = new Guacamole.Mouse(displayElement);
	const sendMouse = (mouseState) => {
		display.showCursor(false);
		client.sendMouseState(mouseState);
	};
	mouse.onmousedown = mouse.onmouseup = mouse.onmousemove = sendMouse;

	const keyboard = new Guacamole.Keyboard(document);
	keyboard.onkeydown = (sym) => {
		if (state.active === app.name)
			client.sendKeyEvent(1, sym);
	};
	keyboard.onkeyup = (sym) => {
		if (state.active === app.name)
			client.sendKeyEvent(0, sym);
	};
	session.keyboard = keyboard;
}

function cleanupSession(app) {
	const session = state.sessions.get(app);
	if (!session)
		return;
	try { session.client.disconnect(); } catch (e) { /* egal */ }
	if (session.keyboard)
		session.keyboard.onkeydown = session.keyboard.onkeyup = null;
}

function closeSession(app) {
	const session = state.sessions.get(app);
	if (!session)
		return;
	cleanupSession(app);
	session.element.remove();
	session.tab.remove();
	state.sessions.delete(app);
	if (state.active === app)
		showHome();
}

/* ---------------------------------------------------------------- Menüs */

function hidePopups() {
	$("menu").classList.add("hidden");
	$("account").classList.add("hidden");
}

function setup() {
	$("login-form").addEventListener("submit", login);
	$("login-user").addEventListener("input", updateLoginButton);
	$("login-pass").addEventListener("input", updateLoginButton);

	$("home-tab").onclick = showHome;
	$("view-grid").onclick = () => {
		$("apps").className = "apps grid";
		$("view-grid").classList.add("active");
		$("view-list").classList.remove("active");
	};
	$("view-list").onclick = () => {
		$("apps").className = "apps list";
		$("view-list").classList.add("active");
		$("view-grid").classList.remove("active");
	};

	$("menu-button").onclick = (event) => {
		event.stopPropagation();
		$("account").classList.add("hidden");
		$("menu").classList.toggle("hidden");
	};
	$("avatar").onclick = (event) => {
		event.stopPropagation();
		$("menu").classList.add("hidden");
		$("account").classList.toggle("hidden");
	};
	document.addEventListener("click", hidePopups);
	$("menu").onclick = (event) => {
		const action = event.target.dataset.action;
		if (action === "fullscreen") {
			if (document.fullscreenElement)
				document.exitFullscreen();
			else
				document.documentElement.requestFullscreen();
		} else if (action === "about") {
			alert("weston-rail-web – RemoteApps von weston-mirror RAIL im Browser");
		}
		hidePopups();
	};
	$("account").onclick = (event) => {
		if (event.target.dataset.action === "logout")
			logout();
	};

	$("connect-cancel").onclick = () => {
		$("connect-dialog").classList.add("hidden");
		$("connect-cancel").textContent = "Abbrechen";
		if (state.active === null)
			showHome();
	};
	$("connect-details").onclick = () => $("connect-log").classList.toggle("hidden");

	window.addEventListener("resize", () => {
		if (state.active)
			resize(state.sessions.get(state.active));
	});
	window.addEventListener("beforeunload", () => {
		for (const app of Array.from(state.sessions.keys()))
			cleanupSession(app);
	});
}

async function start() {
	setup();
	const me = await api("/api/me");
	if (me.body && me.body.user) {
		state.user = me.body.user;
		await showApps();
	} else {
		showLogin(null);
	}
}

start();
