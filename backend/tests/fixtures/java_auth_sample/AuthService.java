package com.example.security;

import android.os.Binder;

public class AuthService {
    
    public void checkPermission(String permission, int uid) {
        int callingUid = Binder.getCallingUid();
        if (callingUid != uid) {
            throw new SecurityException("Permission denied for uid: " + callingUid);
        }
        enforcePermission(permission);
    }
    
    private void enforcePermission(String permission) {
        if (!hasPermission(permission)) {
            throw new SecurityException("Missing permission: " + permission);
        }
    }
    
    private boolean hasPermission(String permission) {
        return permissionStore.check(permission);
    }
}
