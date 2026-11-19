package org.cecoverlan.tv;

import android.accessibilityservice.AccessibilityService;
import android.util.Log;
import android.view.KeyEvent;
import android.view.accessibility.AccessibilityEvent;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Sends every key of the TV's remote to the receiver on the computer (see receiver/app_source.py). */
public class KeyService extends AccessibilityService {
    private static final String TAG = "CecOverLan";
    private static final Pattern PATH = Pattern.compile("mActiveRoutingPath:\\s*(0x[0-9a-fA-F]+)");

    private final BlockingQueue<String> queue = new LinkedBlockingQueue<>(256);
    private volatile boolean running;
    private volatile boolean machineOnScreen;   // the TV shows the computer: keys are consumed here
    private final java.util.HashMap<Integer, Long> held = new java.util.HashMap<>();   // keys that are down
    private volatile int gateKnown = -1;        // -1 not read yet, 0 no, 1 yes

    @Override
    protected void onServiceConnected() {
        running = true;
        new Thread(this::sendLoop, "cec-send").start();
        new Thread(this::gateLoop, "cec-gate").start();
        Log.i(TAG, "service connected, receiver " + Config.HOST + ":" + Config.PORT);
    }

    @Override
    public boolean onUnbind(android.content.Intent intent) {
        running = false;
        return super.onUnbind(intent);
    }

    @Override
    protected boolean onKeyEvent(KeyEvent e) {
        int action = e.getAction();
        boolean consume = Config.CONSUME && machineOnScreen;
        if (e.getRepeatCount() > 0) return consume;          // getevent never printed auto-repeat
        // the remote shows up as two input devices and many keys arrive from both: act on the first DOWN/UP only
        int id = e.getScanCode() != 0 ? e.getScanCode() : 1000 + e.getKeyCode();
        long now = System.currentTimeMillis();
        if (action == KeyEvent.ACTION_DOWN) {
            Long since = held.get(id);
            if (since != null && now - since < 2000) return consume;   // shortcut: a lost UP blocks the key for 2 s at most
            held.put(id, now);
        } else if (action == KeyEvent.ACTION_UP && held.remove(id) == null) {
            return consume;
        }
        String name = KeyNames.of(e.getScanCode());
        if (name == null) name = "KEY_ANDROID_" + e.getKeyCode();   // a key Android made itself (no Linux code): bindable too
        Log.i(TAG, "key code=" + e.getKeyCode() + " scan=" + e.getScanCode() + " name=" + name
                + " action=" + action + " dev=" + e.getDeviceId() + " consume=" + consume);
        if (name != null && (action == KeyEvent.ACTION_DOWN || action == KeyEvent.ACTION_UP)) {
            queue.offer(name + (action == KeyEvent.ACTION_DOWN ? " DOWN" : " UP"));   // a full queue drops: never blocks the TV
        }
        return consume;
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        // diagnosis for the gate: which window comes to the front when the TV changes input
        String w = "WIN " + event.getPackageName() + " " + event.getClassName() + " " + event.getText();
        queue.offer(w.replaceAll("[^ -~]", "?").substring(0, Math.min(w.length(), 150)));   // the receiver logs it
    }
    @Override public void onInterrupt() { }

    /** One TCP connection to the receiver; reconnects with a growing pause. */
    private void sendLoop() {
        long pause = 1000;
        while (running) {
            try (Socket s = new Socket()) {
                s.connect(new InetSocketAddress(Config.HOST, Config.PORT), 5000);
                s.setSoTimeout(5000);
                OutputStream out = s.getOutputStream();
                out.write(("HELLO " + Config.TOKEN + "\n").getBytes("US-ASCII"));
                out.flush();
                String ok = new BufferedReader(new InputStreamReader(s.getInputStream(), "US-ASCII")).readLine();
                if (!"OK".equals(ok)) throw new java.io.IOException("receiver said " + ok);
                Log.i(TAG, "connected to the receiver");
                pause = 1000;
                queue.clear();                                // keys pressed while disconnected are stale
                if (gateKnown >= 0) queue.offer(gateLine());  // a new connection starts without knowing the state
                while (running) {
                    String line = queue.poll(5, TimeUnit.SECONDS);
                    out.write(((line != null ? line : "PING") + "\n").getBytes("US-ASCII"));
                    out.flush();
                }
            } catch (Exception ex) {
                Log.i(TAG, "receiver link: " + ex);
                try { Thread.sleep(pause); } catch (InterruptedException ie) { return; }
                pause = Math.min(pause * 2, 30000);
            }
        }
    }

    /** Reads which HDMI path the TV shows (what the receiver used to ask over adb) and reports changes. */
    private void gateLoop() {
        while (running) {
            Boolean now = readGate();
            if (now != null && (gateKnown < 0 || now != machineOnScreen)) {
                machineOnScreen = now;
                gateKnown = now ? 1 : 0;
                queue.offer(gateLine());
            }
            try { Thread.sleep(1500); } catch (InterruptedException ie) { return; }
        }
    }

    private String gateLine() { return "GATE " + (gateKnown == 1 ? "yes" : "no"); }

    private int gateMisses;

    private Boolean readGate() {
        String out = "", err = "";
        try {
            Process p = Runtime.getRuntime().exec(new String[]{"dumpsys", "hdmi_control"});
            out = slurp(p.getInputStream());
            err = slurp(p.getErrorStream());
            Matcher m = PATH.matcher(out);
            if (m.find()) { gateMisses = 0; return m.group(1).equalsIgnoreCase(Config.ACTIVE_PATH); }
        } catch (Exception ex) {
            err = ex.toString();
        }
        if (gateMisses++ % 20 == 0) {   // say why, now and then: this tells whether an app may read the HDMI state
            Log.i(TAG, "gate: no routing path; stdout=" + out.length() + " bytes '" + head(out) + "' stderr='" + head(err) + "'");
        }
        return null;
    }

    private static String slurp(java.io.InputStream in) throws java.io.IOException {
        StringBuilder sb = new StringBuilder();
        BufferedReader r = new BufferedReader(new InputStreamReader(in));
        String line;
        while ((line = r.readLine()) != null && sb.length() < 4000) sb.append(line).append('\n');
        return sb.toString();
    }

    private static String head(String s) { return s.length() > 400 ? s.substring(0, 400).replace('\n', ' ') : s.replace('\n', ' '); }
}
