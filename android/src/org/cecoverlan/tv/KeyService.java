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

/** Sends every key of the TV's remote to the receiver on the computer (see receiver/app_source.py). */
public class KeyService extends AccessibilityService {
    private static final String TAG = "CecOverLan";
    // keys the TV always keeps, so the remote can still switch it off, leave the input and change the volume (the TV profile)
    private static final java.util.Set<String> TV_KEEPS = new java.util.HashSet<>(java.util.Arrays.asList(Config.KEEP_KEYS));

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
        String named = KeyNames.of(e.getScanCode());
        boolean consume = Config.CONSUME && machineOnScreen && !TV_KEEPS.contains(named);
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
        String name = named;
        if (name == null) name = "KEY_ANDROID_" + e.getKeyCode();   // a key Android made itself (no Linux code): bindable too
        Log.i(TAG, "key code=" + e.getKeyCode() + " scan=" + e.getScanCode() + " name=" + name
                + " action=" + action + " dev=" + e.getDeviceId() + " consume=" + consume);
        if (name != null && (action == KeyEvent.ACTION_DOWN || action == KeyEvent.ACTION_UP)) {
            queue.offer(name + (action == KeyEvent.ACTION_DOWN ? " DOWN" : " UP"));   // a full queue drops: never blocks the TV
        }
        return consume;
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) { refreshForeground(); }

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

    /** Looks at what is in front every 1.5 s (events refresh it sooner); the gate follows from it. */
    private void gateLoop() {
        while (running) {
            refreshForeground();
            try { Thread.sleep(1500); } catch (InterruptedException ie) { return; }
        }
    }

    private String lastForeground = "";
    private long lastRefresh;
    private volatile boolean guessNow;

    /** Sends the line about the window in front when it changes (events come in bursts, so it is throttled),
     *  and tells the receiver when the machine appears on or leaves the screen. */
    private synchronized void refreshForeground() {
        long now = android.os.SystemClock.uptimeMillis();
        if (now - lastRefresh < 150) return;
        lastRefresh = now;
        String fg = foreground();
        if (!fg.equals(lastForeground)) { lastForeground = fg; queue.offer("WIN " + fg); }
        if (guessNow != machineOnScreen || gateKnown < 0) {
            machineOnScreen = guessNow;
            gateKnown = guessNow ? 1 : 0;
            queue.offer(gateLine());
        }
    }

    /**
     * What is in front on the TV, names only. The gate is a guess for the machine being shown: the TV's
     * Live TV app in front (it hosts the HDMI inputs), no tuner window, and nothing with input focus
     * (a focused row means a menu or panel is open on top). dumpsys hdmi_control is closed to apps.
     */
    private String foreground() {
        StringBuilder sb = new StringBuilder();
        try {
            android.view.accessibility.AccessibilityNodeInfo root = getRootInActiveWindow();
            String pkg = root == null ? "null" : String.valueOf(root.getPackageName());
            boolean focus = root != null && root.findFocus(android.view.accessibility.AccessibilityNodeInfo.FOCUS_INPUT) != null;
            boolean dvb = false;
            StringBuilder wins = new StringBuilder();
            for (android.view.accessibility.AccessibilityWindowInfo w : getWindows()) {
                android.view.accessibility.AccessibilityNodeInfo r = w.getRoot();
                String wp = r == null ? "?" : String.valueOf(r.getPackageName());
                for (String marker : Config.TUNER_MARKERS) if (wp.contains(marker)) dvb = true;
                wins.append(" t").append(w.getType()).append(w.isActive() ? "*" : "").append(':').append(wp.replaceAll("^.*\\.", ""));
            }
            guessNow = java.util.Arrays.asList(Config.HDMI_HOSTS).contains(pkg) && !dvb && !focus;
            sb.append("fg=").append(pkg).append(" focus=").append(focus ? "yes" : "no").append(" dvb=").append(dvb ? "yes" : "no")
              .append(" gate?=").append(guessNow ? "yes" : "no").append(" |").append(wins);
        } catch (Exception ex) {
            sb.append("error ").append(ex);
        }
        String s = sb.toString().replaceAll("[^ -~]", "?");
        return s.substring(0, Math.min(s.length(), 190));
    }

    private String gateLine() { return "GATE " + (gateKnown == 1 ? "yes" : "no"); }
}
