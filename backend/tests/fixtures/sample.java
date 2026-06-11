package com.patchquest.sample;

import java.util.List;
import java.util.HashMap;

public class UserService {
    public void createUser(String name) {
    }

    public String getUser(int id) {
        return "user-" + id;
    }

    private void validate(String input) {
    }
}

interface Repository {
    void save(Object entity);
    Object findById(int id);
}
